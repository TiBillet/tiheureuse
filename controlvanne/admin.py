from .models import Card, Debimetre, Fut, HistoriqueFut, RfidSession, TireuseBec
from .forms import TireuseBecForm
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.contrib import admin, messages
from django.utils.html import format_html
import csv


@admin.register(Fut)
class FutAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "brasseur",
        "type_biere",
        "degre_alcool",
        "volume_fut_l",
        "quantite_stock",
        "prix_achat",
    )
    list_editable = ("quantite_stock", "prix_achat")
    list_filter = ("type_biere", "brasseur")
    search_fields = ("nom", "brasseur")


class HistoriqueFutInline(admin.TabularInline):
    model = HistoriqueFut
    extra = 0
    readonly_fields = (
        "fut",
        "mis_en_service_le",
        "retire_le",
        "volume_initial_l",
        "volume_final_l",
        "volume_consomme",
    )
    fields = readonly_fields
    ordering = ("-mis_en_service_le",)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Volume initial (L)")
    def volume_initial_l(self, obj):
        return f"{float(obj.volume_initial_ml) / 1000:.1f} L"

    @admin.display(description="Volume final (L)")
    def volume_final_l(self, obj):
        if obj.volume_final_ml is not None:
            return f"{float(obj.volume_final_ml) / 1000:.1f} L"
        return "En service"

    @admin.display(description="Consommé (L)")
    def volume_consomme(self, obj):
        v = obj.volume_consomme_l
        if v is not None:
            return f"{v:.1f} L"
        return "—"


@admin.register(HistoriqueFut)
class HistoriqueFutAdmin(admin.ModelAdmin):
    list_display = (
        "tireuse_bec",
        "fut",
        "mis_en_service_le",
        "retire_le",
        "volume_initial_l",
        "volume_final_l",
        "volume_consomme",
    )
    list_filter = ("tireuse_bec", "fut__type_biere")
    date_hierarchy = "mis_en_service_le"
    readonly_fields = (
        "tireuse_bec",
        "fut",
        "mis_en_service_le",
        "retire_le",
        "volume_initial_ml",
        "volume_final_ml",
    )

    @admin.display(description="Volume initial (L)")
    def volume_initial_l(self, obj):
        return f"{float(obj.volume_initial_ml) / 1000:.1f} L"

    @admin.display(description="Volume final (L)")
    def volume_final_l(self, obj):
        if obj.volume_final_ml is not None:
            return f"{float(obj.volume_final_ml) / 1000:.1f} L"
        return "En service"

    @admin.display(description="Consommé (L)")
    def volume_consomme(self, obj):
        v = obj.volume_consomme_l
        if v is not None:
            return f"{v:.1f} L"
        return "—"


@admin.register(TireuseBec)
class TireuseBecAdmin(admin.ModelAdmin):
    inlines = [HistoriqueFutInline]
    form = TireuseBecForm
    actions = ["push_kiosk_url", "push_reload", "push_refresh"]
    list_display = (
        "name_with_uuid",
        "fut_actif",
        "debimetre",
        "nom_boisson",
        "monnaie",
        "prix_litre",
        "col_25cl",
        "col_33cl",
        "col_50cl",
        "volume_restant_cl",
        "seuil_mini_cl",
        "appliquer_reserve",
        "enabled",
        "notes",
    )
    list_editable = (
        "fut_actif",
        "debimetre",
        "nom_boisson",
        "monnaie",
        "prix_litre",
        "enabled",
    )
    search_fields = ("nom_tireuse", "nom_boisson", "notes")

    @admin.display(description="Name")
    def name_with_uuid(self, obj):
        return format_html('<span title="UUID: {}">{}</span>', obj.uuid, obj.nom_tireuse)

    @admin.display(description="UUID")
    def uuid_readonly(self, obj):
        return format_html(
            '<code class="uuid-copy" style="cursor:pointer;padding:4px 8px;background:#f0f0f0;border-radius:4px;font-size:12px;" onclick="navigator.clipboard.writeText(\'{}\');alert(\'UUID copié!\')" title="Cliquer pour copier">{}</code>',
            obj.uuid,
            obj.uuid,
        )

    def get_readonly_fields(self, request, obj=None):
        return ("uuid", "col_25cl", "col_33cl", "col_50cl", "volume_restant_cl", "seuil_mini_cl") + super().get_readonly_fields(request, obj)

    @admin.display(description="Volume restant (cl)", ordering="reservoir_ml")
    def volume_restant_cl(self, obj):
        return f"{float(obj.reservoir_ml) / 10:.0f} cl"

    @admin.display(description="Seuil mini (cl)", ordering="seuil_mini_ml")
    def seuil_mini_cl(self, obj):
        return f"{float(obj.seuil_mini_ml) / 10:.0f} cl"

    def _prix_volume(self, obj, cl):
        from decimal import Decimal
        if obj.prix_litre and obj.prix_litre > 0:
            val = (obj.prix_litre * Decimal(str(cl)) / 100).quantize(Decimal("0.01"))
            return f"{val} {obj.monnaie}"
        return "—"

    @admin.display(description="25 cl")
    def col_25cl(self, obj):
        return self._prix_volume(obj, 25)

    @admin.display(description="33 cl")
    def col_33cl(self, obj):
        return self._prix_volume(obj, 33)

    @admin.display(description="50 cl")
    def col_50cl(self, obj):
        return self._prix_volume(obj, 50)

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
            "volume_start_cl",
            "volume_end_cl",
            "volume_servi_cl",
        ]
    )
    for s in queryset:
        w.writerow(
            [
                s.id,
                s.uid,
                s.tireuse_bec.nom_tireuse,
                s.liquid_label_snapshot,
                s.label_snapshot,
                s.authorized,
                s.started_at,
                s.ended_at,
                (s.duration_seconds or ""),
                f"{s.volume_start_ml / 10:.1f}",
                f"{s.volume_end_ml / 10:.1f}",
                f"{s.volume_delta_ml / 10:.1f}",
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
        "volume_servi_cl",
        "label_snapshot",
    )

    @admin.display(description="Volume servi (cl)", ordering="volume_delta_ml")
    def volume_servi_cl(self, obj):
        return f"{obj.volume_delta_ml / 10:.1f}"
    list_filter = ("authorized", "tireuse_bec")
    search_fields = (
        "uid",
        "label_snapshot",
        "tireuse_bec__name",
        "liquid_label_snapshot",
    )
    date_hierarchy = "started_at"
    actions = [export_sessions_csv]
