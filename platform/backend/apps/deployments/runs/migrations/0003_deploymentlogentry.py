# AUTOKOM — journaux de déploiement filtrés
import uuid
import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('deployments', '0002_initial')]
    operations = [
        migrations.AddField(model_name='deploymentrun', name='next_poll_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='deploymentrun', name='cancel_requested', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='deploymentrun', name='console_offset', field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name='deploymentrun', name='console_pending', field=models.CharField(max_length=2000, blank=True)),
        migrations.CreateModel(name='DeploymentLogEntry', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('sequence', models.PositiveIntegerField()),
            ('level', models.CharField(max_length=10, default='INFO')),
            ('message', models.CharField(max_length=800)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('deployment', models.ForeignKey(to='deployments.deploymentrun', on_delete=django.db.models.deletion.CASCADE, related_name='log_entries')),
        ], options={'db_table': 'deployments_logentry', 'ordering': ['sequence']}),
        migrations.AddConstraint(model_name='deploymentlogentry', constraint=models.UniqueConstraint(fields=('deployment','sequence'), name='unique_deploy_log_sequence')),
    ]
