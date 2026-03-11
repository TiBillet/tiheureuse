from django import forms
from .models import TireuseBec


class TireuseBecForm(forms.ModelForm):
    class Meta:
        model = TireuseBec
        fields = ["name", "liquid_label", "unit_label", "unit_ml", "enabled", "notes"]
