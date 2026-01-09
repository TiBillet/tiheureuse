from uuid import uuid4

from django.db import models
from django.utils import timezone
from decimal import Decimal

class Card(models.Model):
    uid = models.CharField(max_length=32, unique=True, help_text="UID hex sans espaces")
    label = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to   = models.DateTimeField(null=True, blank=True)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                  help_text="Solde en unité (ex: patate)")

    def is_valid_now(self):
        now = timezone.now()
        return self.is_active and (not self.valid_from or now>=self.valid_from) and (not self.valid_to or now<=self.valid_to)
    def __str__(self): return self.label or self.uid

class TireuseBec(models.Model):
    # Bec physique
    #TODO: passer en uuid unique pour sélectionner la tireuse plutôt que le slug
    # uuid = models.UUIDField(default=uuid4, primary_key=True)

    slug = models.SlugField(max_length=50, unique=True, help_text="Identifiant technique: ex. 'Biere', 'soft'")

    enabled = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    #TODO: a supprimer ? Remplacer par une foreignKey Produit qui comporte le nom et le prix/litre
    liquid_label = models.CharField(max_length=100, default="Liquide", help_text="Nom affiché du liquide")
    unit_label = models.CharField(max_length=20, default="patate",
                                  help_text="Nom de l'unité de solde (ex: patate)")

    #TODO: a supprimer ?
    unit_ml = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("100.00"),
                                  help_text="Millilitres par unité (ex: 100.00 ml = 10 cL)")

    #TODO faire une classe par constructeur
    #TODO : Récupérer le FlowRate a travers foreignKey constructeur gere cote django

    #TODO: Esce utile ? a supprimer si non
    agent_base_url = models.CharField(
        max_length=200, blank=True, default="http://192.168.1.56:5000",
        help_text="URL de l'agent Flask sur le Pi (ex: http://pi:5000)")

    # TODO: Passer en entier avec modulo si besoin ?
    reservoir_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"),
                                       help_text="Volume courant en ml (décrémenté en temps réel)")
    seuil_mini_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"),
                                           help_text="Seuil bas en ml (on réserve ce volume)")
    appliquer_reserve = models.BooleanField(default=True, help_text="Appliquer la réserve (stock - seuil)")


    def __str__(self):
        return self.slug

class RfidSession(models.Model):
    # presence continue d'une carte (de present=True a present=False)
    uid = models.CharField(max_length=32, db_index=True)
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.SET_NULL, related_name='sessions')
    label_snapshot = models.CharField(max_length=100, blank=True, help_text="Copie du label au début")
    authorized = models.BooleanField(default=False)
    tireuse_bec = models.ForeignKey(TireuseBec, on_delete=models.CASCADE, related_name="sessions", null=True, blank=True)
    liquid_label_snapshot = models.CharField(max_length=100, blank=True, help_text="Copie du nom du liquide au début")
    unit_label_snapshot = models.CharField(max_length=20, blank=True, default="")
    unit_ml_snapshot = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("100.00"))
    allowed_ml_session = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    charged_units = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at   = models.DateTimeField(null=True, blank=True, db_index=True)
    volume_start_ml = models.FloatField(default=0.0)
    volume_end_ml   = models.FloatField(default=0.0)
    volume_delta_ml = models.FloatField(default=0.0)
    dernier_volume_ml = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    last_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ['-started_at']

    @property
    def duration_seconds(self):
        if not self.ended_at: return None
        return (self.ended_at - self.started_at).total_seconds()

    def close_with_volume(self, end_volume_ml: float):
        """Clôt la session et calcule le delta (>=0)."""
        self.ended_at = timezone.now()
        self.volume_end_ml = float(end_volume_ml or 0.0)
        self.volume_delta_ml = max(0.0, self.volume_end_ml - self.volume_start_ml)
        self.save()

    def __str__(self):
        status = "OPEN" if not self.ended_at else "CLOSED"
        return f"{self.tireuse_bec.slug}:{self.uid} [{status}] {self.started_at:%Y-%m-%d %H:%M:%S}"