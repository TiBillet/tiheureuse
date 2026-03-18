from uuid import uuid4

from django.db import models
from django.utils import timezone
from decimal import Decimal


class Card(models.Model):
    uid = models.CharField(max_length=32, unique=True, help_text="UID hex sans espaces")
    label = models.CharField("Nom carte", max_length=100, blank=True)
    is_active = models.BooleanField("Active", default=True)
    valid_from = models.DateTimeField("Valide depuis", null=True, blank=True)
    valid_to = models.DateTimeField("Fin de validité", null=True, blank=True)
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Solde en unité (ex: patate)",
    )

    class Meta:
        verbose_name = "Carte"
        verbose_name_plural = "Cartes"

    def is_valid_now(self):
        now = timezone.now()
        return (
            self.is_active
            and (not self.valid_from or now >= self.valid_from)
            and (not self.valid_to or now <= self.valid_to)
        )

    def __str__(self):
        return self.label or self.uid


class Debimetre(models.Model):
    name = models.CharField(
        "Modèle",
        max_length=100,
        help_text="Modèle du débitmètre (ex: YF-S201, FS300A)",
    )
    flow_calibration_factor = models.FloatField(
        "Facteur de calibration",
        default=6.5,
        help_text="Facteur de calibration (Hz par L/min) — 1 L = facteur × 60 impulsions",
    )

    class Meta:
        verbose_name = "Débitmètre"
        verbose_name_plural = "Débitmètres"

    def __str__(self):
        return f"{self.name} (factor={self.flow_calibration_factor})"


class TireuseBec(models.Model):
    uuid = models.UUIDField(default=uuid4, primary_key=True, editable=False)

    nom_tireuse = models.CharField(max_length=50, help_text="Nom affiché: ex. 'Bière', 'Soft'")

    enabled = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    # TODO: a supprimer ? Remplacer par une foreignKey Produit qui comporte le nom et le prix/litre
    nom_boisson = models.CharField(
        max_length=100, default="Liquide", help_text="Nom affiché du liquide"
    )
    monnaie = models.CharField(
        max_length=20,
        default="patate",
        help_text="Nom de l'unité de solde (ex: patate)",
    )

    prix_litre = models.DecimalField(
        "Prix au litre",
        max_digits=8,
        decimal_places=2,
        default=Decimal("10.00"),
        help_text="Unités de monnaie par litre (ex: 4 patates/L → 25cl = 1 patate)",
    )

    @property
    def unit_ml(self) -> Decimal:
        """ml par unité de monnaie — calculé depuis prix_litre. Utilisé par le Pi."""
        if self.prix_litre and self.prix_litre > 0:
            return (Decimal("1000") / self.prix_litre).quantize(Decimal("0.01"))
        return Decimal("100.00")

    debimetre = models.ForeignKey(
        "Debimetre",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tireuses",
        help_text="Débitmètre associé (détermine le facteur de calibration)",
    )

    # TODO: Passer en entier avec modulo si besoin ?
    reservoir_ml = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Volume courant en ml (décrémenté en temps réel)",
    )
    seuil_mini_ml = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Seuil bas en ml (on réserve ce volume)",
    )
    appliquer_reserve = models.BooleanField(
        default=True, help_text="Appliquer la réserve (stock - seuil)"
    )

    class Meta:
        verbose_name = "Tireuse"
        verbose_name_plural = "Tireuses"

    def __str__(self):
        return self.nom_tireuse


class RfidSession(models.Model):
    # presence continue d'une carte (de present=True a present=False)
    uid = models.CharField(max_length=32, db_index=True)
    card = models.ForeignKey(
        Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    label_snapshot = models.CharField(
        "Nom carte", max_length=100, blank=True, help_text="Copie du label au début"
    )
    authorized = models.BooleanField("En service", default=False)
    tireuse_bec = models.ForeignKey(
        TireuseBec,
        on_delete=models.CASCADE,
        related_name="sessions",
        null=True,
        blank=True,
        verbose_name="Nom tireuse",
    )
    liquid_label_snapshot = models.CharField(
        "Nom boisson", max_length=100, blank=True, help_text="Copie du nom du liquide au début"
    )
    unit_label_snapshot = models.CharField(max_length=20, blank=True, default="")
    unit_ml_snapshot = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("100.00")
    )
    allowed_ml_session = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    charged_units = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    started_at = models.DateTimeField("Début", default=timezone.now, db_index=True)
    ended_at = models.DateTimeField("Fin", null=True, blank=True, db_index=True)
    volume_start_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    volume_end_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    volume_delta_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    dernier_volume_ml = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    last_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Session"
        verbose_name_plural = "Sessions"

    @property
    def duration_seconds(self):
        if not self.ended_at:
            return None
        return (self.ended_at - self.started_at).total_seconds()

    def close_with_volume(self, served_volume_ml: float):
        """
        Clôt la session avec le volume cumulatif servi depuis le début de la session.
        `served_volume_ml` est le volume total versé (Pi: flow_meter.volume_l()*1000 - session_start_vol),
        cohérent avec ce que views.py stocke dans volume_delta_ml.
        """
        self.ended_at = timezone.now()
        vol = Decimal(str(float(served_volume_ml or 0))).quantize(Decimal("0.01"))
        self.volume_delta_ml = max(Decimal("0.00"), vol)
        self.volume_end_ml = self.volume_delta_ml
        self.save()

    def __str__(self):
        status = "OPEN" if not self.ended_at else "CLOSED"
        return f"{self.tireuse_bec.nom_tireuse}:{self.uid} [{status}] {self.started_at:%Y-%m-%d %H:%M:%S}"
