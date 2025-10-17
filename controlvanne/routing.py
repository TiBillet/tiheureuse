from django.urls import path
from .consumers import PanelConsumer
websocket_urlpatterns = [ path("ws/panel/", PanelConsumer.as_asgi()), ]