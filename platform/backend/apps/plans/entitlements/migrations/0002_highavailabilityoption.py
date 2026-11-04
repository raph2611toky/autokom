# AUTOKOM — Option premium HA (aucun paiement automatique)
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('plan_entitlements', '0001_initial'),
        ('environments', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name='HighAvailabilityOption', fields=[
            ('id', models.UUIDField(primary_key=True, serialize=False, default=uuid.uuid4, editable=False)),
            ('desired_replicas', models.PositiveSmallIntegerField(default=2)),
            ('status', models.CharField(max_length=12, default='PENDING', choices=[('PENDING', 'En attente de validation commerciale'), ('ACTIVE', 'Activée après validation'), ('REJECTED', 'Refusée'), ('EXPIRED', 'Expirée')])),
            ('billing_reference', models.CharField(max_length=120, blank=True, help_text='Référence de facturation vérifiée hors API client.')),
            ('activated_at', models.DateTimeField(null=True, blank=True)),
            ('expires_at', models.DateTimeField(null=True, blank=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('approved_by', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, blank=True, null=True, related_name='approved_ha_options')),
            ('environment', models.OneToOneField(to='environments.environment', on_delete=django.db.models.deletion.PROTECT, related_name='ha_option')),
        ], options={'db_table': 'plans_highavailabilityoption'}),
    ]
