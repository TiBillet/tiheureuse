from django.urls import path
from . import views
urlpatterns = [
#    path("", views.index, name="index"),
    path("api/rfid/event/", views.api_rfid_event, name="api_rfid_event"),
    path("api/rfid/authorize", views.api_rfid_authorize, name="api_rfid_authorize"),
    path("", views.panel_multi, name="panel"),
]