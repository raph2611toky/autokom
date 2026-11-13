import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def mark_historical_feedback(apps, schema_editor):
    Run = apps.get_model('deployments', 'DeploymentRun')
    Run.objects.using(schema_editor.connection.alias).filter(
        mode_snapshot='DEVSECOPS', status__in=['SUCCEEDED','FAILED','BLOCKED']).update(
            security_notifications_checked_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [('deployments', '0005_security_feedback')]
    operations = [
        migrations.AddField(model_name='deploymentrun', name='security_notifications_checked_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='deploymentrun', name='sca_report',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='deploymentrun', name='rollback_source',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                    related_name='restorations', to='deployments.deploymentrun')),
        migrations.RunPython(mark_historical_feedback, migrations.RunPython.noop),
    ]
