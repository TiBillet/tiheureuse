#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import TireuseBec, RfidSession

SAFE = re.compile(r"[^A-Za-z0-9._-]")
def _safe(slug: str) -> str:
    return (SAFE.sub("", (slug or "").strip().lower())[:80]) or "all"

def snapshot_for_bec(tb: TireuseBec):
    # essaie de pré-remplir avec l’état de session ouverte si elle existe
    open_s = RfidSession.objects.filter(tireuse_bec=tb, ended_at__isnull=True)\
                                .order_by("-started_at").first()
    return {
        "tireuse_bec": tb.slug,
        "liquid_label": tb.liquid_label,
        "present": bool(open_s and open_s.uid),
        "authorized": bool(open_s.authorized) if open_s else False,
        "vanne_ouverte": False,  # le serveur ne sait pas piloter le GPIO du Pi
        "volume_ml": float(open_s.volume_end_ml if open_s else 0.0),
        "debit_l_min": 0.0,
        "message": "",
        "uid": open_s.uid if open_s else None,
    }
# détecter un rename de slug
@receiver(pre_save, sender=TireuseBec)
def _remember_old_slug(sender, instance: TireuseBec, **kwargs):
    if not instance.pk:
        instance._old_slug = None
        return
    try:
        old = TireuseBec.objects.get(pk=instance.pk)
        instance._old_slug = old.slug
    except TireuseBec.DoesNotExist:
        instance._old_slug = None

@receiver(post_save, sender=TireuseBec)
def on_tireusebec_changed(sender, instance: TireuseBec, created, **kwargs):
    # push uniquement pour le groupe ciblé
    payload = snapshot_for_bec(instance)
    ch = get_channel_layer()
    new_safe = _safe(instance.slug)
    # pousser le snapshot vers le NOUVEAU groupe
    async_to_sync(ch.group_send)(
        f"rfid_state.{new_safe}",
        {"type": "state.update", "payload": payload}
    )
#    slug_group = f"rfid_state.{_safe(instance.slug)}"
#    async_to_sync(ch.group_send)(slug_group, {"type": "state.update", "payload": payload})
#    async_to_sync(ch.group_send)("rfid_state.all", {"type": "state.update", "payload": payload})

    # si rename: pousser aussi vers l'ancien groupe (pour écrans encore abonnés)
    old_slug = getattr(instance, "_old_slug", None)
    if old_slug and old_slug != instance.slug:
        async_to_sync(ch.group_send)(
            f"rfid_state.{_safe(old_slug)}",

            {"type": "state.update", "payload": {"redirect_to": instance.slug}}
        )