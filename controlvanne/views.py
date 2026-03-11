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
    # ----Debug---
    print(f"👀 DATA REÇU DU PI rfid_authorize (Brut) : {request.body}")

    # le Pi envoie du JSON
    try:
        data = json.loads(request.body)
        print(f"👀 DATA DECODÉ : {data}")
    except:
        print("Pas de JSON valide")
    # ---Fin Debug---

    # 1. Parsing des données reçues
    try:
        data = json.loads(request.body)
        uid_raw = data.get("uid")
        # On récupère l'ID de la tireuse (envoyé par le Pi) pour savoir où afficher l'erreur
        target_uuid = data.get("tireuse_id") or data.get("tireuse_bec") or "all"
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
    return JsonResponse(
        {
            "authorized": True,
            "session_id": open_session.id,
            "balance": str(card.balance),
            "liquid_label": tireuse_bec.liquid_label,
            "unit_label": tireuse_bec.unit_label,
            "unit_ml": float(tireuse_bec.unit_ml),
        }
    )


@csrf_exempt
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
    uid = raw_uid.upper().replace(":", "").replace(" ", "")  # _norm_uid simplifié

    event_data = data.get("data", {})
    session_id = event_data.get("session_id")

    # Calcul Volume : On convertit le float reçu en Decimal
    volume_float = float(event_data.get("volume_ml", 0.0))
    current_vol = Decimal(f"{volume_float}").quantize(Decimal("0.01"))

    # Initialisation de la variable tireuse_bec
    # tireuse_bec = None
    target_uuid_raw = data.get("tireuse_bec") or data.get("tireuse_id")
    tireuse_bec = None
    session = None

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
    if event_type == "auth_fail":
        message = data.get("message", "Non autorisé")
        print(f"EVENT AUTH_FAIL reçu pour {tireuse_bec.name}")
        _ws_push(
            tireuse_bec,
            {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": True,
                "authorized": False,
                "vanne_ouverte": False,
                "uid": uid,
                "message": message,
            },
        )
        return JsonResponse({"status": "ok"})

    # --- CAS 2 : RETRAIT CARTE (RESET ECRAN) ---
    if event_type == "card_removed":
        _ws_push(
            tireuse_bec,
            {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": False,
                "uid": "",
                "message": "En attente...",
                "authorized": False,
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
        _ws_push(
            tireuse_bec,
            {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": True,
                "authorized": True,
                "uid": uid,
                "liquid_label": session.liquid_label_snapshot,
                "balance": str(session.card.balance) if session.card else "0.00",
                "volume_ml": 0.0,
                "message": "Servez-vous !",
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

            # 3. Fin de session (FACTURATION)
            session_done = False
            charged_display = "0.00"
            balance_display = str(session.card.balance) if session.card else "0.00"

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

            # 3. On prépare les données (le payload)
            data_to_send = {
                "tireuse_bec": tireuse_bec.name,
                "tireuse_bec_uuid": str(tireuse_bec.uuid),
                "present": True if not session_done else False,
                "authorized": True,
                "vanne_ouverte": True,
                "session_done": session_done,
                "uid": uid,
                "liquid_label": session.liquid_label_snapshot or "Bière",
                "volume_ml": float(current_vol),
                "charged": charged_display,
                "balance": balance_display,
                "reservoir_ml": float(tireuse_bec.reservoir_ml),
                "message": f"Terminé : {current_vol:.0f} ml"
                if session_done
                else "Service en cours...",
            }

            # 4. On envoie. IMPORTANT :
            # - "type" doit correspondre au nom de la méthode dans Consumer (`async def state_update`)
            # - Le consumer attend les données dans une clé "payload"
            print(f"🚀 ENVOI WS vers '{tireuse_bec.name}' ET vers 'ALL'")

            # 1. Envoi au canal SPÉCIFIQUE (pour l'écran du Pi)
            # ex: rfid_state.narval
            async_to_sync(channel_layer.group_send)(
                f"rfid_state.{tireuse_bec.uuid}",
                {"type": "state_update", "payload": data_to_send},
            )

            # 2. Envoi au canal GÉNÉRAL (pour le Dashboard PC)
            async_to_sync(channel_layer.group_send)(
                "rfid_state.all", {"type": "state_update", "payload": data_to_send}
            )

    return JsonResponse({"status": "ok"})
