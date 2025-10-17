from django.contrib import admin
from .models import Card, RfidSession, TireuseBec
from django.http import HttpResponse
import csv

@admin.register(TireuseBec)
class TireuseBecAdmin(admin.ModelAdmin):
    list_display=("slug","liquid_label","unit_label","unit_ml","enabled","notes")
    list_editable = ("liquid_label","enabled","notes")
    search_fields = ("slug","liquid_label")


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