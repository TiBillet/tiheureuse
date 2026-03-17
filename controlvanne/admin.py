from .models import Card, Debimetre, RfidSession, TireuseBec
from .forms import TireuseBecForm
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.contrib import admin, messages
from django.utils.html import format_html
import csv


@admin.register(TireuseBec)
class TireuseBecAdmin(admin.ModelAdmin):
    form = TireuseBecForm
    actions = ["push_kiosk_url", "push_reload", "push_refresh"]
    list_display = (
        "name_with_uuid",
        "debimetre",
        "liquid_label",
        "reservoir_ml",
        "seuil_mini_ml",
        "appliquer_reserve",
        "unit_label",
        "unit_ml",
        "enabled",
        "notes",
    )
    list_editable = (
        "debimetre",
        "liquid_label",
        "unit_label",
        "unit_ml",
        "enabled",
    )
    search_fields = ("name", "liquid_label", "notes")

    @admin.display(description="Name")
    def name_with_uuid(self, obj):
        return format_html('<span title="UUID: {}">{}</span>', obj.uuid, obj.name)

    @admin.display(description="UUID")
    def uuid_readonly(self, obj):
        return format_html(
            '<code class="uuid-copy" style="cursor:pointer;padding:4px 8px;background:#f0f0f0;border-radius:4px;font-size:12px;" onclick="navigator.clipboard.writeText(\'{}\');alert(\'UUID copié!\')" title="Cliquer pour copier">{}</code>',
            obj.uuid,
            obj.uuid,
        )

    def get_readonly_fields(self, request, obj=None):
        return ("uuid",) + super().get_readonly_fields(request, obj)

    # def push_kiosk_url(self, request, queryset):
    #     ch = get_channel_layer()
    #     n = 0
    #     for tb in queryset:
    #         url = f"{request.scheme}://{request.get_host()}/?tireuse_bec={tb.uuid}"
    #         async_to_sync(ch.group_send)(
    #             f"rfid_state.{tb.uuid}",
    #             {"type": "state_update", "payload": {"kiosk_url": url}},
    #         )
    #         n += 1
    #     self.message_user(request, f"Nouvelle URL envoyée à {n} kiosque(s) via WebSocket.")
    #
    # push_kiosk_url.short_description = "Envoyer la bonne URL au kiosque (WebSocket)"

    def push_reload(self, request, queryset):
        ch = get_channel_layer()
        n = 0
        for tb in queryset:
            async_to_sync(ch.group_send)(
                f"rfid_state.{tb.uuid}",
                {"type": "state_update", "payload": {"kiosk_reload": True}},
            )
            n += 1
        self.message_user(request, f"Mise à jour de l'affichage envoyé à {n} kiosque(s).")

    push_reload.short_description = "Mise à jour de l'affichage du kiosque"

    def push_refresh(self, request, queryset):
        # pousse un snapshot vers les panneaux abonnés
        from .signals import snapshot_for_bec
        import sys

        print(
            f"🚀 PUSH_REFRESH APPELE avec {queryset.count()} tireuse(s)",
            file=sys.stderr,
        )

        ch = get_channel_layer()
        if not ch:
            print("❌ ERREUR: channel_layer est None!", file=sys.stderr)
            self.message_user(
                request, "Erreur: channel_layer non disponible", level=messages.ERROR
            )
            return

        n = 0
        for tb in queryset:
            payload = snapshot_for_bec(tb)
            group_name = f"rfid_state.{tb.uuid}"
            print(f"🚀 PUSH_REFRESH vers {group_name}: {payload}", file=sys.stderr)

            try:
                async_to_sync(ch.group_send)(
                    group_name,
                    {"type": "state_update", "payload": payload},
                )
                print(f"✅ Message envoye au groupe {group_name}", file=sys.stderr)
            except Exception as e:
                print(f"❌ ERREUR envoi au groupe {group_name}: {e}", file=sys.stderr)

            # Envoyer aussi au groupe ALL pour l'interface admin
            try:
                async_to_sync(ch.group_send)(
                    "rfid_state.all",
                    {"type": "state_update", "payload": payload},
                )
                print(f"✅ Message envoye au groupe rfid_state.all", file=sys.stderr)
            except Exception as e:
                print(f"❌ ERREUR envoi au groupe all: {e}", file=sys.stderr)

            n += 1
        self.message_user(request, f"Snapshot poussé à {n} tireuse(s).")

    push_refresh.short_description = "Pousser une mise à jour au panneau"


@admin.register(Debimetre)
class DebitmetreAdmin(admin.ModelAdmin):
    list_display = ("name", "flow_calibration_factor")
    list_editable = ("flow_calibration_factor",)


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("uid", "label", "balance", "is_active", "valid_from", "valid_to")
    search_fields = ("uid", "label")
    list_filter = ("is_active",)


def export_sessions_csv(modeladmin, request, queryset):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="rfid_sessions.csv"'
    w = csv.writer(resp)
    w.writerow(
        [
            "id",
            "uid",
            "tireuse_bec",
            "liquid",
            "label_snapshot",
            "authorized",
            "started_at",
            "ended_at",
            "duration_s",
            "volume_start_ml",
            "volume_end_ml",
            "volume_delta_ml",
        ]
    )
    for s in queryset:
        w.writerow(
            [
                s.id,
                s.uid,
                s.tireuse_bec.name,
                s.liquid_label_snapshot,
                s.label_snapshot,
                s.authorized,
                s.started_at,
                s.ended_at,
                (s.duration_seconds or ""),
                f"{s.volume_start_ml:.1f}",
                f"{s.volume_end_ml:.1f}",
                f"{s.volume_delta_ml:.1f}",
            ]
        )
    return resp


export_sessions_csv.short_description = "Exporter en CSV"


@admin.register(RfidSession)
class RfidSessionAdmin(admin.ModelAdmin):
    list_display = (
        "tireuse_bec",
        "liquid_label_snapshot",
        "uid",
        "authorized",
        "started_at",
        "ended_at",
        "volume_delta_ml",
        "label_snapshot",
    )
    list_filter = ("authorized", "tireuse_bec")
    search_fields = (
        "uid",
        "label_snapshot",
        "tireuse_bec__name",
        "liquid_label_snapshot",
    )
    date_hierarchy = "started_at"
    actions = [export_sessions_csv]
