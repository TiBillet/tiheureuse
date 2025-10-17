
# controlvanne/migrations/00XX_fill_tireuse_bec_default.py
from django.db import migrations, models

def set_default_tireuse_bec(apps, schema_editor):
    TireuseBec = apps.get_model('controlvanne', 'TireuseBec')
    RfidSession = apps.get_model('controlvanne', 'RfidSession')

    # 1) Crée / récupère la tireuse 'default'
    bec, _ = TireuseBec.objects.get_or_create(
        slug='default',
        defaults={'liquid_label': 'Liquide', 'enabled': True}
    )

    # 2) Assigne la tireuse aux sessions orphelines
    RfidSession.objects.filter(tireuse_bec__isnull=True).update(tireuse_bec=bec)

    # 3) Remplit le snapshot de liquide (pour toutes les sessions)
    to_update = []
    qs = RfidSession.objects.select_related('tireuse_bec').only('id', 'tireuse_bec_id', 'liquid_label_snapshot')
    for s in qs:
        label = s.tireuse_bec.liquid_label if s.tireuse_bec else 'Liquide'
        if s.liquid_label_snapshot != label:
            s.liquid_label_snapshot = label
            to_update.append(s)
    if to_update:
        RfidSession.objects.bulk_update(to_update, ['liquid_label_snapshot'])

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('controlvanne', '0005_rename_dispenser_to_tireuse_bec'),  # ← remplace par le nom réel
    ]

    operations = [
        # Data migration d’abord
        migrations.RunPython(set_default_tireuse_bec, reverse_code=noop),

        # Puis on rend le champ non-nullable (note: on importe et utilise "models", pas "migrations.models")
        migrations.AlterField(
            model_name='rfidsession',
            name='tireuse_bec',
            field=models.ForeignKey(
                to='controlvanne.tireusebec',
                on_delete=models.deletion.CASCADE,
                related_name='sessions',
                null=False,
            ),
        ),
    ]

