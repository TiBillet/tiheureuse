from django import forms
from .models import TireuseBec


class TireuseBecForm(forms.ModelForm):
    class Meta:
        model = TireuseBec
        fields = ["nom_tireuse", "nom_boisson", "monnaie", "prix_litre_override", "enabled", "notes"]
