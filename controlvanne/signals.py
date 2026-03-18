#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import TireuseBec, RfidSession


def _safe(name: str) -> str:
    return (name or "").strip().lower()[:80] or "all"


def snapshot_for_bec(tb: TireuseBec):
    open_s = (
        RfidSession.objects.filter(tireuse_bec=tb, ended_at__isnull=True)
        .order_by("-started_at")
        .first()
    )
    return {
        "tireuse_bec": tb.nom_tireuse,
        "tireuse_bec_uuid": str(tb.uuid),
        "liquid_label": tb.nom_boisson,
        "present": bool(open_s and open_s.uid),
        "authorized": bool(open_s.authorized) if open_s else False,
        "vanne_ouverte": False,
        "volume_ml": float(open_s.volume_end_ml if open_s else 0.0),
        "debit_l_min": 0.0,
        "message": "",
        "uid": open_s.uid if open_s else None,
    }


@receiver(pre_save, sender=TireuseBec)
def _remember_old_name(sender, instance: TireuseBec, **kwargs):
    if not instance.pk:
        instance._old_name = None
        return
    try:
        old = TireuseBec.objects.get(pk=instance.pk)
        instance._old_name = old.nom_tireuse
    except TireuseBec.DoesNotExist:
        instance._old_name = None


@receiver(post_save, sender=TireuseBec)
def on_tireusebec_changed(sender, instance: TireuseBec, created, **kwargs):
    payload = snapshot_for_bec(instance)
    ch = get_channel_layer()

    # Envoi au canal spécifique de cette tireuse
    async_to_sync(ch.group_send)(
        f"rfid_state.{instance.uuid}", {"type": "state_update", "payload": payload}
    )

    # Envoi au canal général (dashboard admin)
    async_to_sync(ch.group_send)(
        "rfid_state.all", {"type": "state_update", "payload": payload}
    )

    # Si renommage : notifier les écrans encore abonnés à l'ancien nom
    old_name = getattr(instance, "_old_name", None)
    if old_name and old_name != instance.nom_tireuse:
        async_to_sync(ch.group_send)(
            f"rfid_state.{instance.uuid}",
            {"type": "state_update", "payload": {"redirect_to": instance.name}},
        )
