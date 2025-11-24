import json, re, time
from smtplib import quoteaddr

from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Card, RfidSession, TireuseBec
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F

def _dec(x, d="0.00"):  # helper
    try: return Decimal(str(x))
    except: return Decimal(d)
def index(request): return render(request, "controlvanne/index.html")

def panel_multi(request):
    slug_focus = (request.GET.get("tireuse_bec") or "").strip().lower()
    show_all = (slug_focus in ("", "all"))
    becs = TireuseBec.objects.order_by("slug")
    return render(request, "controlvanne/panel_bootstrap.html", {
        "becs": becs,
        # slug ciblé pour afficher qu’un Pi ou tout
        "slug_focus": "" if show_all else slug_focus,
        "show_all": show_all,
    })

def _check_key(request):
    key = request.headers.get("X-API-Key") or request.GET.get("key")
    want = getattr(settings, "AGENT_SHARED_KEY", None)
    return (not want) or (key == want)
def _norm_uid(uid: str) -> str: return re.sub(r"[^0-9A-Fa-f]","", uid or "").upper()

SAFE = re.compile(r"[^A-Za-z0-9._-]")
def _safe(slug: str) -> str:
    return SAFE.sub("", (slug or "").strip().lower())[:80] or "all"

@csrf_exempt
def api_rfid_authorize(request):
    if not _check_key(request): return HttpResponseForbidden("forbidden")
    uid = request.GET.get("uid")
    if not uid and request.body:
        try: uid = json.loads(request.body).get("uid")
        except Exception: pass
    uid = _norm_uid(uid)
    if not uid: return HttpResponseBadRequest("missing uid")
#    from .models import Card

    # lire la tireuse ciblée pour connaître la conversion
    tb_slug = request.GET.get("tireuse_bec") or "default"
    tb = TireuseBec.objects.filter(slug__iexact=tb_slug).first()
    card = Card.objects.filter(uid__iexact=uid).first()
    ok = bool(card and card.is_valid_now())

    unit_ml = _dec(tb.unit_ml if tb else "100.00")
    unit_label = tb.unit_label if tb else "patate"
    balance = _dec(card.balance if card else "0.00")
    quota_ml = (balance * unit_ml) if ok else Decimal("0.00")
    enough_funds = quota_ml > 0

    if card and card.is_valid_now() and unit_ml > 0:
        quota_ml = (card.balance * unit_ml).quantize(Decimal("0.01"))

    if tb.appliquer_reserve:
        restant_ml = (tb.reservoir_ml - tb.seuil_mini_ml).quantize(Decimal("0.01"))
        if restant_ml < 0: restant_ml = Decimal("0.00")
    else:
        restant_ml = Decimal("9e9")  # "infini"
    seuil_autorise=min(quota_ml, restant_ml)


    return JsonResponse({"authorized": ok, "uid": uid,
                         "label": (card.label if card else None),
                         "reason": (None if ok else ("Carte inconnue" if not card else "inactive")),
                         "balance": str(balance),
                         "unit_label": unit_label,
                         "unit_ml": float(unit_ml),
                         "seuil_autorisé": float(seuil_autorise),  # quota alloué pour cette session
                         "enough_funds": bool(enough_funds),
                         })

@csrf_exempt
def api_rfid_event(request):
    """
    Reçoit du Pi des événements live:
      - present=True, authorized, vanne_ouverte, volume_ml (total), debit_l_min, message
      - present=False -> clôture la session (débit carte, payload session_done)
    En parallèle, décrémente `TireuseBec.reservoir_ml` en temps réel (delta entre deux messages).
    Diffuse en WebSocket sur:
      - rfid_state.<slug>
      - (optionnel) rfid_state.all
    """
    if not _check_key(request):
        return HttpResponseForbidden("forbidden")
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    # ---- Parse JSON ----
    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        return HttpResponseBadRequest("invalid json")

    uid_raw      = data.get("uid")
    present      = bool(data.get("present", False))
    authorized   = bool(data.get("authorized", False))
    vanne_ouverte= bool(data.get("vanne_ouverte", False))
    volume_ml_in = float(data.get("volume_ml") or 0.0)         # total compteur côté Pi
    debit_l_min  = float(data.get("debit_l_min") or 0.0)
    message      = (data.get("message") or "").strip()
    label_hint   = (data.get("liquid_label") or "").strip()

    uid = _norm_uid(uid_raw)

    # ---- Identification de la tireuse ----
    bec_in = (data.get("tireuse_bec") or "defaut").strip().lower()
    bec_slug = _safe(bec_in)
    tireuse_bec, created = TireuseBec.objects.get_or_create(
        slug=bec_slug,
        defaults={"liquid_label": (label_hint or "Liquide")}
    )
    if label_hint and tireuse_bec.liquid_label != label_hint:
        tireuse_bec.liquid_label = label_hint
        tireuse_bec.save(update_fields=["liquid_label"])

    # ---- Session ouverte (une seule max par bec) ----
    open_s = RfidSession.objects.filter(
        tireuse_bec=tireuse_bec, ended_at__isnull=True
    ).order_by("-started_at").first()

    # Prépare payload commun
    payload = {
        "ts": time.time(),
        "tireuse_bec": tireuse_bec.slug,
        "liquid_label": tireuse_bec.liquid_label,
        "present": present,
        "authorized": authorized,
        "vanne_ouverte": vanne_ouverte,
        "volume_ml": float(volume_ml_in),
        "debit_l_min": float(debit_l_min),
        "message": message,
    }

    # ==== CAS 1 : Carte présente ====
    if present and uid:
        # Si une session ouverte existe mais pour un autre UID, on la clôt d'abord proprement.
        if open_s and open_s.uid != uid:
            with transaction.atomic():
                s = RfidSession.objects.select_for_update().get(pk=open_s.pk)
                s.volume_end_ml = _dec(volume_ml_in)
                if s.volume_start_ml is None:
                    s.volume_start_ml = _dec
                s.volume_delta_ml = (s.volume_end_ml - (s.volume_start_ml or _dec)).quantize(Decimal("0.01"))
                s.last_message = message
                s.ended_at = timezone.now()
                s.save(update_fields=["volume_end_ml", "volume_delta_ml", "last_message", "ended_at"])
            open_s = None  # on repart sur une nouvelle session

        # (Re)charge la session après éventuelle clôture ci-dessus
        open_s = RfidSession.objects.filter(
            tireuse_bec=tireuse_bec, ended_at__isnull=True
        ).order_by("-started_at").first()

        if not open_s:
            # Nouvelle session
            card_obj = Card.objects.filter(uid__iexact=uid).first()
            unit_ml = _dec(tireuse_bec.unit_ml or "100.00")
            open_s = RfidSession.objects.create(
                tireuse_bec=tireuse_bec,
                liquid_label_snapshot=tireuse_bec.liquid_label,
                uid=uid,
                card=card_obj,
                label_snapshot=(card_obj.label if card_obj else ""),
                unit_label_snapshot=(tireuse_bec.unit_label or "u"),
                unit_ml_snapshot=unit_ml,
                authorized=authorized,
                started_at=timezone.now(),
                volume_start_ml=_dec(volume_ml_in),
                last_reported_ml=_dec(volume_ml_in),
                last_message=message,
            )
        else:
            # Mise à jour live + décrément réservoir par delta
            cur_ml = _dec(volume_ml_in)
            with transaction.atomic():
                s = RfidSession.objects.select_for_update().get(pk=open_s.pk)
                tb = TireuseBec.objects.select_for_update().get(pk=tireuse_bec.pk)

                # Calcul delta depuis dernière mesure
                last = _dec(s.last_reported_ml or s.volume_start_ml or 0)
                inc_ml = (cur_ml - last)
                if inc_ml < 0:
                    inc_ml = _dec  # sécurité si reset compteur côté Pi

                if inc_ml > 0:
                    new_stock = (tb.reservoir_ml - inc_ml)
                    if new_stock < 0:
                        new_stock = _dec
                    tb.reservoir_ml = new_stock
                    tb.save(update_fields=["reservoir_ml"])

                    s.last_reported_ml = cur_ml
                    s.volume_end_ml = cur_ml
                    s.authorized = authorized
                    s.last_message = message
                    s.save(update_fields=["last_reported_ml", "volume_end_ml", "authorized", "last_message"])

        # Ajoute l’état stock dans le payload pour l’affichage
        # (NB : pas sous transaction ici, précision “assez bonne” pour le panel)
        room_after = tireuse_bec.reservoir_ml - tireuse_bec.low_threshold_ml
        if room_after < 0:
            room_after = _dec
        payload.update({
            "reservoir_ml": float(tireuse_bec.reservoir_ml),
            "low_threshold_ml": float(tireuse_bec.low_threshold_ml),
            "room_after_ml": float(room_after),
        })

        # Diffusion WS et réponse
        _ws_push(tireuse_bec.slug, payload)
        return JsonResponse({"ok": True})

    # ==== CAS 2 : Pas de carte (ou UID vide) -> clôture la session si ouverte ====
    if open_s:
        with transaction.atomic():
            s = RfidSession.objects.select_for_update().select_related("tireuse_bec").get(pk=open_s.pk)
            tb = s.tireuse_bec  # verrouillé via select_for_update si on le souhaite

            end_ml = _dec(volume_ml_in)
            start_ml = _dec(s.volume_start_ml or 0)
            delta_ml = (end_ml - start_ml)
            if delta_ml < 0:
                delta_ml = _dec

            s.volume_end_ml = end_ml
            s.volume_delta_ml = delta_ml
            s.last_message = message or "Session terminée"
            s.ended_at = timezone.now()

            # Débit carte si présente
            balance_after = None
            if s.card_id:
                # calcule unités = delta_ml / unit_ml_snapshot (ou unit_ml bec)
                unit_ml = _dec(s.unit_ml_snapshot or tb.unit_ml or "100.00")
                if unit_ml > 0 and delta_ml > 0:
                    units = (delta_ml / unit_ml).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    # débit carte
                    card = Card.objects.select_for_update().get(pk=s.card_id)
                    if units > card.balance:
                        units = card.balance  # plafonne au solde
                    if units > 0:
                        card.balance = (card.balance - units).quantize(Decimal("0.01"))
                        card.save(update_fields=["balance"])
                        s.charged_units = units
                        balance_after = str(card.balance)
                        s.save(update_fields=["charged_units"])

            s.save(update_fields=["volume_end_ml", "volume_delta_ml", "last_message", "ended_at"])

        # Construire payload “fin de session”
        room_after = tireuse_bec.reservoir_ml - tireuse_bec.low_threshold_ml
        if room_after < 0:
            room_after = _dec

        payload.update({
            "present": False,
            "authorized": False,
            "vanne_ouverte": False,
            "message": s.last_message or "Session terminée",
            "session_done": True,
            "session_volume_ml": float(s.volume_delta_ml or 0.0),
            "reservoir_ml": float(tireuse_bec.reservoir_ml),
            "low_threshold_ml": float(tireuse_bec.low_threshold_ml),
            "room_after_ml": float(room_after),
            "balance": balance_after,  # str ou None
        })

        _ws_push(tireuse_bec.slug, payload)
        return JsonResponse({"ok": True})

    # Pas de session ouverte => simple mise à jour “idle”
    _ws_push(tireuse_bec.slug, payload)
    return JsonResponse({"ok": True})


# ---------- Push WebSocket ----------
def _ws_push(slug: str, payload: dict, also_all: bool = True):
    ch = get_channel_layer()
    slug_safe = _safe(slug)
    # groupe ciblé
    async_to_sync(ch.group_send)(
        f"rfid_state.{slug_safe}",
        {"type": "state.update", "payload": payload},
    )
    # groupe ALL (optionnel)
    if also_all:
        async_to_sync(ch.group_send)(
            "rfid_state.all",
            {"type": "state.update", "payload": payload},
        )

