from urllib.parse import parse_qs
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
import re
from controlvanne.models import TireuseBec, RfidSession

SAFE = re.compile(r"[^A-Za-z0-9._-]")
# mise en forme standardisee
def sanitize(slug: str) -> str:
    slug = (slug or "").strip().lower()
    return SAFE.sub("", slug)[:80] or "all"   # marge <100

class PanelConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        qs = parse_qs(self.scope.get("query_string", b"").decode() if self.scope.get("query_string") else "")
        only = sanitize((qs.get("tireuse_bec", [""])[0] or ""))
        self.group = f"rfid_state.{only or 'ALL'}"
        ###Log
        print(f"[WS] connect group={self.group} qs={qs}")
        ###
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        init_payload = await self._initial_payload(only)
        if init_payload:
            await self.send_json(init_payload)


    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def state_update(self, event):
        await self.send_json(event["payload"])


    # helpers
    @database_sync_to_async
    def _initial_payload(self, only_slug: str):
        if not only_slug or only_slug == "all":
            return None
        tb = TireuseBec.objects.filter(slug__iexact=only_slug).first()
        if not tb:
            # la tireuse n'existe pas encore en DB -> on envoie au moins le slug
            return {
                "tireuse_bec": only_slug,
                "liquid_label": "Liquide",
                "present": False,
                "authorized": False,
                "vanne_ouverte": False,
                "volume_ml": 0.0,
                "debit_l_min": 0.0,
                "message": "",
            }

        # on essaye d’afficher un état de base (si une session ouverte existe)
        open_s = RfidSession.objects.filter(tireuse_bec=tb, ended_at__isnull=True).order_by("-started_at").first()
        return {
            "tireuse_bec": tb.slug,
            "liquid_label": tb.liquid_label,
            "present": bool(open_s and open_s.uid),
            "authorized": bool(open_s.authorized) if open_s else False,
            "vanne_ouverte": False,
            "volume_ml": float(open_s.volume_end_ml if open_s else 0.0),
            "debit_l_min": 0.0,
            "message": open_s.last_message if open_s else "",
            "uid": open_s.uid if open_s else None,
        }