import re
from django import forms
from .models import TireuseBec

SAFE = re.compile(r"[^a-z0-9._-]")

class TireuseBecForm(forms.ModelForm):
    class Meta:
        model = TireuseBec
        fields = ["slug", "liquid_label", "unit_label", "unit_ml", "enabled", "notes"]

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        slug = SAFE.sub("", slug)
        if not slug:
            raise forms.ValidationError("Slug requis (caractères autorisés: a-z, 0-9, . _ -)")
        return slug
