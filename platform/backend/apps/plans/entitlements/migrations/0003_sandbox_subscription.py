# AUTOKOM simulated payment: audited, reversible, always clearly labelled TEST.
import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('plan_entitlements', '0002_highavailabilityoption'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SandboxCheckout',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('plan_code', models.CharField(max_length=16)),
                ('period', models.CharField(max_length=12, choices=[('monthly', 'Mensuel'), ('annual', 'Annuel')])),
                ('amount_ariary', models.PositiveIntegerField()),
                ('reference', models.CharField(max_length=120, unique=True)),
                ('idempotency_key', models.UUIDField()),
                ('status', models.CharField(max_length=12, choices=[('ACCEPTED', 'Simulation acceptée'), ('REVOKED', 'Simulation révoquée')], default='ACCEPTED')),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sandbox_checkouts', to='accounts.user')),
                ('environment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sandbox_checkouts', to='environments.environment')),
                ('resource_plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sandbox_checkouts', to='plan_catalog.resourceplan')),
            ],
            options={'db_table': 'plans_sandboxcheckout'},
        ),
        migrations.AddConstraint(
            model_name='sandboxcheckout',
            constraint=models.UniqueConstraint(fields=('environment', 'created_by', 'idempotency_key'), name='unique_sandbox_checkout_request'),
        ),
        migrations.AddField(
            model_name='applicationentitlement', name='sandbox_checkout',
            field=models.ForeignKey(to='plan_entitlements.sandboxcheckout', on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name='entitlements'),
        ),
        migrations.AddField(
            model_name='highavailabilityoption', name='sandbox_checkout',
            field=models.ForeignKey(to='plan_entitlements.sandboxcheckout', on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name='ha_options'),
        ),
        migrations.AlterField(
            model_name='applicationentitlement', name='grant_source',
            field=models.CharField(max_length=12, default='SYSTEM', choices=[
                ('SYSTEM', 'Allocation gratuite automatique'),
                ('ADMIN', 'Attribution manuelle par administrateur'),
                ('PAYMENT', 'Paiement futur (non intégré)'),
                ('TEST', 'Abonnement simulé (sans transaction réelle)'),
            ]),
        ),
    ]
