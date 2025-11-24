from .models import Card, RfidSession, TireuseBec
from .forms import TireuseBecForm
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.contrib import admin, messages
from django.conf import settings
import csv, re
import requests

AGENT_KEY = getattr(settings, "AGENT_SHARED_KEY", "changeme")

SAFE = re.compile(r"[^A-Za-z0-9._-]")
def _safe(slug: str) -> str:
    return (SAFE.sub("", (slug or "").strip().lower())[:80]) or "all"

@admin.register(TireuseBec)
class TireuseBecAdmin(admin.ModelAdmin):
    form = TireuseBecForm
    list_display = ("slug", "agent_base_url", "liquid_label","reservoir_ml", "seuil_mini_ml", "appliquer_reserve", "unit_label", "unit_ml", "enabled", "notes")
    list_editable = ("liquid_label", "agent_base_url", "unit_label", "unit_ml", "enabled")
    search_fields = ("slug", "liquid_label", "notes")
    list_filter = ("enabled",)
    ordering = ("slug",)
    fieldsets = (
        ("Boisson", {"fields": ("slug", "liquid_label")}),
        ("Unité / Conversion", {"fields": ("unit_label", "unit_ml")}),
        ("Stock et seuil", {"fields": ("reservoir_ml","seuil_mini_ml", "appliquer_reserve")}),
        ("Autres", {"fields": ("enabled", "notes")}),
    )
    actions = ["push_kiosk_url",
    "push_refresh",
    ]
    def push_kiosk_url(self, request, queryset):
        ok, ko = 0, 0
        for tb in queryset:
            try:
            # construit URL cible sur le Pi
                target = f"{request.scheme}://{request.get_host()}/?tireuse_bec={tb.slug}"
                endpoint = (tb.agent_base_url or "").rstrip("/") + "/agent/kiosk/set_url"
                r = requests.post(
                    endpoint,
                    json={"url": target},
                    headers={"X-API-Key": AGENT_KEY},
                    timeout=3.0
                )
                if r.ok and r.json().get("ok"):
                    ok += 1
                else:
                    ko += 1
            except Exception:
                ko += 1
        if ok:
            self.message_user(request, f"KIOSK_URL mis à jour et kiosque relancé pour {ok} bec(s).", level=messages.SUCCESS)
        if ko:
            self.message_user(request, f"Échec sur {ko} bec(s). Vérifie agent_base_url et la clé API.",
                              level=messages.ERROR)
    push_kiosk_url.short_description = "Mettre à jour l'URL du kiosque et redémarrer"


    def push_refresh(self, request, queryset):
    # pousse un snapshot vers les panneaux abonnés
        from .signals import snapshot_for_bec
        ch = get_channel_layer()
        n = 0
        for tb in queryset:
            payload = snapshot_for_bec(tb)
            async_to_sync(ch.group_send)(f"rfid_state.{_safe(tb.slug)}",
                                     {"type": "state.update", "payload": payload})
            n += 1
        self.message_user(request, f"Snapshot poussé à {n} tireuse(s).")

    push_refresh.short_description = "Pousser une mise à jour au panneau"

@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display=("uid","label","balance","is_active","valid_from","valid_to")
    search_fields=("uid","label")
    list_filter=("is_active",)

def export_sessions_csv(modeladmin, request, queryset):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="rfid_sessions.csv"'
    w = csv.writer(resp)
    w.writerow(["id","uid","tireuse_bec","liquid","label_snapshot","authorized","started_at","ended_at","duration_s","volume_start_ml","volume_end_ml","volume_delta_ml"])
    for s in queryset:
        w.writerow([
            s.id, s.uid,s.tireuse_bec.slug, s.liquid_label_snapshot, s.label_snapshot, s.authorized,
            s.started_at, s.ended_at, (s.duration_seconds or ""),
            f"{s.volume_start_ml:.1f}", f"{s.volume_end_ml:.1f}", f"{s.volume_delta_ml:.1f}",
        ])
    return resp
export_sessions_csv.short_description = "Exporter en CSV"

@admin.register(RfidSession)
class RfidSessionAdmin(admin.ModelAdmin):
    list_display = ("tireuse_bec", "liquid_label_snapshot", "uid", "authorized", "started_at", "ended_at", "volume_delta_ml", "label_snapshot")
    list_filter  = ("authorized", "tireuse_bec")
    search_fields = ("uid","label_snapshot","tireuse_bec__slug","liquid_label_snapshot")
    date_hierarchy = "started_at"
    actions = [export_sessions_csv]