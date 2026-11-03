"""Align SecretReference with its encrypted-at-rest storage model."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('secrets', '0001_initial')]

    operations = [
        migrations.AlterField(
            model_name='secretreference',
            name='vault_reference',
            field=models.CharField(
                max_length=500, blank=True,
                help_text='Référence future vers un coffre externe.'
            ),
        ),
        migrations.AddField(
            model_name='secretreference',
            name='encrypted_value',
            field=models.TextField(
                blank=True,
                help_text='Valeur chiffrée Fernet ; jamais retournée par API.'
            ),
        ),
    ]
