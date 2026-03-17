import json, re, time
from smtplib import quoteaddr
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils import timezone
from asgiref.sync import async_to_sync
from .models import Card, RfidSession, TireuseBec
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F
from channels.layers import get_channel_layer


def _dec(x, d="0.00"):  # helper
    try:
        return Decimal(str(x))
    except:
        return Decimal(d)


def index(request):
    return render(request, "controlvanne/index.html")


def panel_multi(request):
    tireuse_bec = request.GET.get("tireuse_bec")
    print(f"DEBUG: tireuse_bec = '{tireuse_bec}'")
    if tireuse_bec:
        becs = None
        try:
            from uuid import UUID

            UUID(tireuse_bec)
            becs = TireuseBec.objects.filter(uuid=tireuse_bec)
            print(f"DEBUG: cherche par UUID, trouvé: {becs.count()}")
        except (ValueError, TypeError):
            print(f"DEBUG: pas un UUID, chercher par nom")
            becs = TireuseBec.objects.filter(name__iexact=tireuse_bec)
            print(f"DEBUG: trouvé par nom: {becs.count()}")
        if not becs:
            becs = TireuseBec.objects.all()
            print(f"DEBUG: fallback all, trouvé: {becs.count()}")
    else:
        becs = TireuseBec.objects.all()
    print(f"DEBUG: becs total: {becs.count()}")

    # Déterminer le slug_focus pour le WebSocket
    slug_focus = tireuse_bec if tireuse_bec else "all"

    return render(
        request,
        "controlvanne/panel_bootstrap.html",
        {
            "becs": becs,
            "slug_focus": slug_focus,
        },
    )


def _check_key(request):
    key = request.headers.get("X-API-Key") or request.GET.get("key")
    want = getattr(settings, "AGENT_SHARED_KEY", None)
    return (not want) or (key == want)


def _norm_uid(uid: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", uid or "").upper()


SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    return (name or "").strip().lower()[:80] or "all"


def _ws_push(tireuse_bec, data):
    """
    Envoie un message WebSocket à un groupe spécifique ET au groupe 'all'.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    # Nom du groupe avec UUID (accepte soit un objet TireuseBec soit un UUID string)
    if hasattr(tireuse_bec, "uuid"):
        group_uuid = str(tireuse_bec.uuid)
    else:
        group_uuid = str(tireuse_bec)

    group_name = f"rfid_state.{group_uuid}"

    # Structure du message pour le consumer Django Channels
    # "type": "state_update" appelle la méthode state_update du consumer
    message_structure = {"type": "state_update", "payload": data}

    print(f"📡 WS PUSH vers {group_name} : {data.get('message')}")

    # 1. Envoi au canal spécifique
    async_to_sync(channel_layer.group_send)(group_name, message_structure)

    # 2. Envoi au canal général (rfid_state.all) pour le dashboard admin
    # if safe_name != "all":
    async_to_sync(channel_layer.group_send)("rfid_state.all", message_structure)


@csrf_exempt
def ping(request):
    """Répond au test de connexion du Raspberry Pi"""
    return JsonResponse({"status": "pong", "message": "Server online"})


@csrf_exempt
def api_rfid_authorize(request):
    """Vérifie si une carte est autorisée et crée une session."""
    # 1. Parsing des données reçues
    try:
        data = json.loads(request.body)
        uid_raw = data.get("uid")
        # On récupère l'ID de la tireuse (envoyé par le Pi) pour savoir où afficher l'erreur
        target_uuid = data.get("tireuse_bec") or "all"
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "JSON invalide"}, status=400)

    # Debug Log
    print(f"🔍 AUTH REQUEST: UID={uid_raw} sur BEC={target_uuid}")

    # 2. Vérification Clé API
    if not _check_key(request):
        return JsonResponse({"error": "Clé API invalide"}, status=403)

    if not uid_raw:
        return JsonResponse({"error": "UID manquant"}, status=400)

    uid = _norm_uid(uid_raw)

    # 3. Vérification Carte
    card = Card.objects.filter(uid__iexact=uid, is_active=True).first()

    # --- CAS ERREUR : CARTE INCONNUE / EXPIRÉE ---
    if not card or not card.is_valid_now():
        msg = "Carte inconnue ou expirée"
        print(f"⛔ REFUS {uid} : {msg}")

        # affichage Rouge :
        _ws_push(
            target_uuid,
            {
                "tireuse_bec": target_uuid,
                "tireuse_bec_uuid": target_uuid,
                "present": True,
                "authorized": False,  # Rouge
                "vanne_ouverte": False,
                "uid": uid,
                "message": msg,
            },
        )
        return JsonResponse({"authorized": False, "error": msg}, status=403)

    # --- CAS ERREUR : SOLDE INSUFFISANT ---
    if card.balance <= 0:
        msg = f"Solde insuffisant ({card.balance}€)"
        print(f"⛔ REFUS {uid} : {msg}")

        _ws_push(
            target_uuid,
            {
                "tireuse_bec": target_uuid,
                "tireuse_bec_uuid": target_uuid,
                "present": True,
                "authorized": False,  # Rouge
                "vanne_ouverte": False,
                "uid": uid,
                "balance": str(card.balance),
                "message": msg,
            },
        )
        return JsonResponse({"authorized": False, "error": msg}, status=403)

    # 4. Gestion de la Session (Succès)
    open_session = RfidSession.objects.filter(card=card, ended_at__isnull=True).first()

    if not open_session:
        # On cherche la tireuse correspondant à l'UUID envoyé par le Pi
        tireuse_bec = TireuseBec.objects.filter(uuid=target_uuid).first()

        # Fallback si UUID inconnu, chercher par nom
        if not tireuse_bec:
            tireuse_bec = TireuseBec.objects.filter(name__iexact=target_uuid).first()

        # Dernier recours
        if not tireuse_bec:
            tireuse_bec = TireuseBec.objects.filter(enabled=True).first()

        if not tireuse_bec:
            return JsonResponse(
                {"authorized": False, "error": "Aucun bec dispo"}, status=500
            )

        # Calcul du volume max autorisé basé sur le solde de la carte
        max_volume_ml = float(card.balance) * float(tireuse_bec.unit_ml)

        # Plafonnement par le stock disponible (réserve)
        # Si appliquer_reserve est activé, on ne peut pas servir plus que
        # (reservoir_ml - seuil_mini_ml) pour préserver la réserve de fond de fût.
        if tireuse_bec.appliquer_reserve and tireuse_bec.seuil_mini_ml > 0:
            stock_disponible_ml = float(
                tireuse_bec.reservoir_ml - tireuse_bec.seuil_mini_ml
            )
            if stock_disponible_ml <= 0:
                # Stock épuisé sous le seuil : refus de service
                return JsonResponse(
                    {
                        "authorized": False,
                        "error": "Stock insuffisant (réserve atteinte)",
                    },
                    status=403,
                )
            max_volume_ml = min(max_volume_ml, stock_disponible_ml)

        # Création session
        open_session = RfidSession.objects.create(
            tireuse_bec=tireuse_bec,
            uid=uid,
            card=card,
            started_at=timezone.now(),
            volume_start_ml=0.0,
            authorized=True,
            liquid_label_snapshot=tireuse_bec.liquid_label,
            label_snapshot=card.label,
            unit_label_snapshot=tireuse_bec.unit_label,
            unit_ml_snapshot=tireuse_bec.unit_ml,
            allowed_ml_session=max_volume_ml,
        )
    else:
        tireuse_bec = open_session.tireuse_bec

    # 5. SUCCÈS : Notification Écran (VERT)
    payload_ws = {
        "tireuse_bec": tireuse_bec.name,
        "tireuse_bec_uuid": str(tireuse_bec.uuid),
        "present": True,
        "authorized": True,  # Vert
        "vanne_ouverte": True,  # Vert
        "uid": uid,
        "liquid_label": tireuse_bec.liquid_label,
        "balance": str(card.balance),
        "message": f"Badge accepté. Solde: {card.balance} €",
    }

    print(f"✅ SUCCÈS {uid} sur {tireuse_bec.name}")

    # On utilise la _ws_push
    _ws_push(tireuse_bec, payload_ws)

    # 6. Réponse HTTP au Pi
    flow_factor = (
        tireuse_bec.debimetre.flow_calibration_factor
        if tireuse_bec.debimetre
        else 6.5
    )
    return JsonResponse(
        {
            "authorized": True,
            "session_id": open_session.id,
            "balance": str(card.balance),
            "liquid_label": tireuse_bec.liquid_label,
            "unit_label": tireuse_bec.unit_label,
            "unit_ml": str(tireuse_bec.unit_ml),
            "flow_calibration_factor": flow_factor,
        }
    )


@csrf_exempt
def api_rfid_event(request):
    """
    Reçoit les événements du Pi Python (start, update, end, auth_fail, card_removed)
    """
    # Debug optionnel
    # print(f"DATA: {request.body}")

    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    # 1. Extraction des données
    event_type = data.get("event_type")

    # Gestion UID (parfois brut, parfois nettoyé, on sécurise)
    raw_uid = data.get("uid", "")
    uid = _norm_uid(raw_uid)

    event_data = data.get("data", {})
    session_id = event_data.get("session_id")

    # Calcul Volume : On convertit le float reçu en Decimal
    volume_float = float(event_data.get("volume_ml", 0.0))
    current_vol = Decimal(f"{volume_float}").quantize(Decimal("0.01"))

    # Débit instantané transmis par le Pi (L/min), maintenant alimenté par FlowMeter.update()
    debit_l_min = float(event_data.get("debit_l_min", 0.0))

    # Initialisation des variables
    target_uuid_raw = data.get("tireuse_bec")
    tireuse_bec = None
    session = None
    solde_epuise = False  # Variable pour suivre si le solde est épuisé

    # 1. ESSAYER DE TROUVER LA SESSION (Cas start, update, end)
    if session_id:
        try:
            session = RfidSession.objects.get(pk=session_id)
            tireuse_bec = session.tireuse_bec
        except RfidSession.DoesNotExist:
            pass

    # 2. SI PAS DE SESSION ID (Cas card_removed ou auth_fail)
    if not tireuse_bec and target_uuid_raw:
        tireuse_bec = TireuseBec.objects.filter(uuid=target_uuid_raw).first()
        if not tireuse_bec:
            tireuse_bec = TireuseBec.objects.filter(
                name__iexact=target_uuid_raw
            ).first()

    # 3. DERNIER RECOURS
    if not tireuse_bec:
        tireuse_bec = TireuseBec.objects.first()

    if not tireuse_bec:
        return JsonResponse(
            {"status": "error", "message": "Aucun bec trouvé"}, status=500
        )
    # =========================================================================
    # LOGIQUE EVENEMENTS
    # =========================================================================

    # --- CAS 1 : IDENTIFIANT REFUSÉ / CARTE REMIS EN ROUGE ---
    # NOTE: Le WebSocket est déjà envoyé par api_rfid_authorize avec le bon message
    # On ne fait rien ici pour éviter le doublon "Carte inconnue" puis "Non autorisé"
    if event_type == "auth_fail":
        print(f"🔴 AUTH_FAIL reçu mais ignoré (déjà géré par api_rfid_authorize)")
        return JsonResponse({"status": "ok"})

    # --- CAS 2 : RETRAIT CARTE (RESET ECRAN) ---
    if event_type == "card_removed":
        # Récupérer la dernière session pour avoir le volume servi et le solde
        last_session = (
            RfidSession.objects.filter(uid=uid, tireuse_bec=tireuse_bec)
            .order_by("-started_at")
            .first()
        )

        # Calculer le volume servi et le solde restant
        volume_served = 0.0
        remaining_balance = None
        if last_session:
            volume_served = float(last_session.volume_delta_ml or 0)
            if last_session.card:
                # Calculer le solde restant (après facturation si terminé)
                if last_session.ended_at:
                    remaining_balance = str(last_session.card.balance)
                else:
                    # Session non terminée, calculer solde estimé
                    unit_ml = last_session.unit_ml_snapshot or Decimal("100.0")
                    if unit_ml > 0 and volume_served > 0:
                        units_consumed = (
                            Decimal(str(volume_served)) / unit_ml
                        ).quantize(Decimal("0.01"))
                        remaining = last_session.card.balance - units_consumed
                        if remaining < 0:
                            remaining = Decimal("0.00")
                        remaining_balance = str(remaining)
                    else:
                        remaining_balance = str(last_session.card.balance)

        print(
            f"🍺 ENVOI CARD_REMOVED - Volume: {volume_served}ml, Solde: {remaining_balance}"
        )
        _ws_push(
            tireuse_bec,
            {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": False,
                "uid": "",
                "message": f"Terminé - Reste: {remaining_balance or '0.00'}€"
                if volume_served > 0
                else "En attente...",
                "authorized": False,
                "volume_ml": volume_served,
                "balance": remaining_balance or "0.00",
            },
        )
        return JsonResponse({"status": "ok"})

    # --- CAS 3 : FLUX (START, UPDATE, END) ---
    # Nécessite une session valide
    if not session_id:
        return JsonResponse({"status": "error", "message": "No session ID"}, status=400)

    try:
        session = RfidSession.objects.get(pk=session_id)
    except RfidSession.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Session not found"}, status=404
        )

    # A. Début de versage
    if event_type == "pour_start":
        # On informe juste l'écran (Vert)
        start_balance = str(session.card.balance) if session.card else "0.00"
        _ws_push(
            tireuse_bec,
            {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": True,
                "authorized": True,
                "uid": uid,
                "liquid_label": session.liquid_label_snapshot,
                "balance": start_balance,
                "volume_ml": 0.0,
                "message": f"Servez-vous ! Solde: {start_balance}€",
            },
        )

    # B. Mise à jour ou Fin
    elif event_type in ["pour_update", "pour_end"]:
        with transaction.atomic():
            # 1. Calculer combien on a versé DEPUIS LA DERNIERE FOIS pour le Stock
            # On utilise volume_delta_ml comme "dernier volume connu"
            val_prev = session.volume_delta_ml
            if val_prev is None:
                previous_vol = Decimal("0.00")
            else:
                # On passe par str() pour convertir float -> Decimal sans erreur
                previous_vol = Decimal(str(val_prev))

            delta_stock = current_vol - previous_vol

            # Mise à jour Stock Tireuse (si positif)
            if delta_stock > 0:
                tb = TireuseBec.objects.select_for_update().get(pk=tireuse_bec.pk)
                tb.reservoir_ml = tb.reservoir_ml - delta_stock
                if tb.reservoir_ml < 0:
                    tb.reservoir_ml = Decimal("0.00")
                tb.save()
                # On met à jour l'objet local pour le renvoyer au WS
                tireuse_bec.reservoir_ml = tb.reservoir_ml

            # 2. Mise à jour Session
            session.volume_delta_ml = current_vol  # Le volume accumulé venant du Pi
            session.last_message = f"Volume: {current_vol} ml"

            # Vérification solde épuisé pendant le service
            solde_epuise = False
            if session.card and session.allowed_ml_session:
                if current_vol >= float(session.allowed_ml_session):
                    solde_epuise = True
                    session.last_message = "Solde épuisé - Vanne fermée"
                    print(
                        f"⚠️ SOLDE ÉPUISÉ pour {uid} - Volume: {current_vol}ml, Max: {session.allowed_ml_session}ml"
                    )

            # 3. Calcul du solde estimé restant pendant le service
            # (avant la facturation finale)
            estimated_balance = str(session.card.balance) if session.card else "0.00"
            if session.card and current_vol > 0:
                unit_ml = session.unit_ml_snapshot or Decimal("100.0")
                if unit_ml > 0:
                    units_consumed = (current_vol / unit_ml).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    remaining = session.card.balance - units_consumed
                    if remaining < 0:
                        remaining = Decimal("0.00")
                    estimated_balance = str(remaining)

            # 4. Fin de session (FACTURATION)
            session_done = False
            charged_display = "0.00"
            balance_display = (
                estimated_balance  # Utiliser le solde estimé pour l'affichage
            )

            if event_type == "pour_end":
                session.ended_at = timezone.now()
                session_done = True

                if session.card:
                    card = Card.objects.select_for_update().get(pk=session.card.pk)
                    unit_ml = session.unit_ml_snapshot or Decimal("100.0")

                    if current_vol > 0 and unit_ml > 0:
                        # Calcul prix
                        units = (current_vol / unit_ml).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )

                        # Plafond solde
                        if units > card.balance:
                            units = card.balance

                            # Débit
                        card.balance -= units
                        card.save()

                        session.charged_units = units
                        charged_display = str(units)
                        balance_display = str(card.balance)

            session.save()

            # 1. On récupère le channel layer
            channel_layer = get_channel_layer()

            # 2. On construit le nom du groupe EXACTEMENT comme dans consumers.py
            group_name = f"rfid_state.{tireuse_bec.uuid}"

            # 3. On prépare les données
            # Si solde épuisé, on force la fermeture de la vanne
            vanne_ouverte = True
            force_close = False
            if solde_epuise:
                vanne_ouverte = False
                force_close = True

            data_to_send = {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": True if not session_done else False,
                "authorized": True,
                "vanne_ouverte": vanne_ouverte,
                "force_close": force_close,
                "session_done": session_done or solde_epuise,
                "uid": uid,
                "liquid_label": session.liquid_label_snapshot or "Bière",
                "volume_ml": float(current_vol),
                "debit_l_min": debit_l_min,
                "charged": charged_display,
                "balance": balance_display,
                "reservoir_ml": float(tireuse_bec.reservoir_ml),
                "message": f"Terminé : {current_vol:.0f} ml"
                if session_done
                else ("Solde épuisé !" if solde_epuise else "Service en cours..."),
            }

            # 4. On envoie.
            # - "type" doit correspondre au nom de la méthode dans Consumer (`async def state_update`)
            # - Le consumer attend les données dans une clé "payload"
            print(f"🚀 ENVOI WS vers '{tireuse_bec.name}' ET vers 'ALL'")

            # 1. Envoi au canal SPÉCIFIQUE (pour l'écran du Pi)

            async_to_sync(channel_layer.group_send)(
                f"rfid_state.{tireuse_bec.uuid}",
                {"type": "state_update", "payload": data_to_send},
            )

            # 2. Envoi au canal GÉNÉRAL (pour le Dashboard PC)
            async_to_sync(channel_layer.group_send)(
                "rfid_state.all", {"type": "state_update", "payload": data_to_send}
            )

    # Réponse au Pi avec indication si fermeture forcée nécessaire
    response_data = {"status": "ok"}
    if solde_epuise:
        response_data["force_close"] = True
        response_data["message"] = "Solde epuise - Fermeture vanne requise"

    return JsonResponse(response_data)
