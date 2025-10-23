from urllib.parse import parse_qs
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import re

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

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def state_update(self, event):
        await self.send_json(event["payload"])