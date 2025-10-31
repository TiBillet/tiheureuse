import json, re, time
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

def _dec(x, d="0.00"):  # helper
    try: return Decimal(str(x))
    except: return Decimal(d)
def index(request): return render(request, "controlvanne/index.html")

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
    allowed_ml = (balance * unit_ml) if ok else Decimal("0.00")
    enough_funds = allowed_ml > 0

    return JsonResponse({"authorized": ok, "uid": uid,
                         "label": (card.label if card else None),
                         "reason": (None if ok else ("Carte inconnue" if not card else "inactive")),
                         "balance": str(balance),
                         "unit_label": unit_label,
                         "unit_ml": float(unit_ml),
                         "allowed_ml": float(allowed_ml),  # quota alloué pour cette session
                         "enough_funds": bool(enough_funds),
                         })

@csrf_exempt
def api_rfid_event(request):
# Recoit du Pi3 et diffuse en WS

    if not _check_key(request): return HttpResponseForbidden("forbidden")
    if request.method != "POST": return HttpResponseBadRequest("POST only")
    try: data = json.loads(request.body or b"{}")
    except Exception: return HttpResponseBadRequest("invalid json")

    uid = _norm_uid(data.get("uid"))
    present = bool(data.get("present", False))
    authorized = bool(data.get("authorized", False))
    vanne_ouverte = bool(data.get("vanne_ouverte", False))
    volume_ml = float(data.get("volume_ml") or 0.0)
    debit_l_min = float(data.get("debit_l_min") or 0.0)
    message = data.get("message") or ""

    card_obj = None

    # identification du distributeur
    slug_in = (data.get("tireuse_bec") )
    bec_slug = (slug_in or "defaut").strip().lower()
    label_hint = (data.get("liquid_label") or "").strip()

    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    client_ip = xff or request.META.get("REMOTE_ADDR") or ""

    agent_base_url = (data.get("agent_base_url") or "").strip()
    if not (agent_base_url.startswith("http") and "0.0.0.0" not in agent_base_url):
    # fallback fiable : reconstruire depuis l’IP source + port défaut 5000
        agent_base_url = f"http://{client_ip}:5000"

    tireuse_bec, created = TireuseBec.objects.get_or_create(slug=bec_slug, defaults={
        "liquid_label": "Liquide"})
    if getattr(tireuse_bec, "agent_base_url", "") != agent_base_url:
        tireuse_bec.agent_base_url = agent_base_url
        tireuse_bec.save(update_fields=["agent_base_url"])


    # --- Gestion de session (1 session ouverte max) ---
    open_s = RfidSession.objects.filter(tireuse_bec=tireuse_bec, ended_at__isnull=True).order_by("-started_at").first()

    if present and uid:
        # nouvelle carte OU première detection : (si open_s d'un autre uid, on l'arrete)
        if open_s and open_s.uid != uid:
            open_s.close_with_volume(volume_ml)
            open_s= None
        # re-fetch après éventuelle clôture
        #open_s = RfidSession.objects.filter(ended_at__isnull=True).order_by("-started_at").first()

        if not open_s:
            card_obj = Card.objects.filter(uid__iexact=uid).first()
            unit_ml = tireuse_bec.unit_ml
            unit_label = tireuse_bec.unit_label
            balance = _dec(card_obj.balance if card_obj else "0.00")
            allowed_ml = (balance * _dec(unit_ml)) if (card_obj and card_obj.is_valid_now()) else Decimal("0.00")
            open_s = RfidSession.objects.create(
                tireuse_bec=tireuse_bec,
                liquid_label_snapshot=tireuse_bec.liquid_label,
                uid=uid,
                card=card_obj,
                label_snapshot=(card_obj.label if card_obj else ""),
                unit_label_snapshot=unit_label,
                unit_ml_snapshot=_dec(unit_ml),
                allowed_ml_session=allowed_ml,
                authorized=authorized,
                started_at=timezone.now(),
                volume_start_ml=volume_ml,
                last_message=message,
            )
        else:
            # mise à jour continue
            card_obj = open_s.card
            open_s.volume_end_ml = volume_ml
            open_s.authorized = authorized
            open_s.last_message = message
            open_s.save(update_fields=["volume_end_ml","authorized","last_message"])

    else:
        # pas de carte présente -> cloturer si session ouverte
        if open_s:
            open_s.last_message = message
            open_s.close_with_volume(volume_ml)
            card_obj = open_s.card
            # carte OK
            if open_s.card_id:
                with transaction.atomic():
                    # verrouille la carte pour MAJ solde
                    card = Card.objects.select_for_update().get(pk=open_s.card_id)
                    unit_ml = _dec(open_s.unit_ml_snapshot or tireuse_bec.unit_ml or "100.00")
                    consumed_ml = _dec(open_s.volume_delta_ml or 0)
                    if consumed_ml > 0 and unit_ml > 0:
                        units = (consumed_ml / unit_ml).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        # plafonne à ce qu'il reste (sécurité)
                        if card.balance < units: units = card.balance
                        if units > 0:
                            card.balance = (card.balance - units).quantize(Decimal("0.01"))
                            card.save(update_fields=["balance"])
                            open_s.charged_units = units
                            open_s.save(update_fields=["charged_units"])
# --- construit le payload SANS risquer UnboundLocalError
    balance_val = None
    if card_obj is not None and hasattr(card_obj, "balance"):
        balance_val = str(card_obj.balance)  # str pour éviter les soucis Decimal JSON

    # push websocket
    payload = {
        "ts": time.time(),
        "uid": uid or None,
        "tireuse_bec": tireuse_bec.slug,
        "liquid_label": tireuse_bec.liquid_label,
        "present": present,
        "authorized": authorized,
        "vanne_ouverte": vanne_ouverte,
        "volume_ml": volume_ml,
        "debit_l_min": debit_l_min,
        "message": message,
        "balance": balance_val,

    }

    ch = get_channel_layer()
    slug_safe = _safe(tireuse_bec.slug)
# 1) Groupe global (pour une page "supervision" qui voit tout)
    async_to_sync(ch.group_send)(
        "rfid_state.all",
        {"type": "state.update", "payload": payload}
    )

# 2) Groupe ciblé (slug de la tireuse courante)
    async_to_sync(ch.group_send)(
        f"rfid_state.{slug_safe}",
        {"type": "state.update", "payload": payload}
    )

    return JsonResponse({"ok": True})
