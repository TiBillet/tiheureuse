import json
from channels.generic.websocket import AsyncWebsocketConsumer
GROUP="rfid_state"
class PanelConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add(GROUP, self.channel_name)
        await self.accept()
    async def disconnect(self, code):
        await self.channel_layer.group_discard(GROUP, self.channel_name)
    async def state_update(self, event):
        await self.send(text_data=json.dumps(event.get("payload", {})))
