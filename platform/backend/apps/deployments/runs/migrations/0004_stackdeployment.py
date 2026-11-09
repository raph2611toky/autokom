import uuid
import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('deployments', '0003_deploymentlogentry'),
                    ('configurations', '0004_servicestack')]
    operations = [migrations.AlterField(model_name='deploymentrun', name='status', field=models.CharField(max_length=12, default='QUEUED', choices=[('QUEUED', 'En attente'),('RUNNING','En cours'),('PREPARED','Candidat privé prêt'),('SUCCEEDED','Réussi'),('FAILED','Échec'),('CANCELLED','Annulé'),('BLOCKED','Bloqué')])), migrations.CreateModel(
        name='StackDeployment',
        fields=[('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('frontend_sha', models.CharField(max_length=40)),
                ('frontend_resources', models.JSONField(default=dict)),
                ('idempotency_key', models.CharField(max_length=150, unique=True)),
                ('status', models.CharField(max_length=12, choices=[('QUEUED','En attente'),('PREPARING','Construction'),('ACTIVATING','Activation HTTPS'),('SUCCEEDED','En ligne'),('FAILED','Échec'),('BLOCKED','À réconcilier')], default='QUEUED')),
                ('error_code', models.CharField(max_length=100, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(null=True, blank=True)),
                ('stack', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='deployments', to='configurations.servicestack')),
                ('backend_run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='backend_stack_execution', to='deployments.deploymentrun')),
                ('frontend_run', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='frontend_stack_execution', to='deployments.deploymentrun')),
                ('frontend_configuration', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stack_frontend_deployments', to='configurations.deploymentconfig'))],
        options={'db_table': 'deployments_stackdeployment'},
    ), migrations.AddIndex(model_name='stackdeployment', index=models.Index(fields=['stack','created_at'], name='stack_created_idx'))]
