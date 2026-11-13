from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('deployments', '0004_stackdeployment')]
    operations = [
        migrations.AddField(model_name='deploymentrun', name='sast_report',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(
            name='SecurityFeedbackAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(max_length=8)),
                ('target', models.PositiveIntegerField(default=0)),
                ('result_number', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='security_feedback',
                                         to='deployments.deploymentrun')),
            ],
        ),
        migrations.AddConstraint(model_name='securityfeedbackaction',
            constraint=models.UniqueConstraint(fields=('run', 'channel', 'target'),
                                               name='unique_autokom_security_delivery')),
    ]
