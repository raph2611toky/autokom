from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('domains', '0003_domain_primary')]
    operations = [
        migrations.AddField(model_name='domain', name='activation_error_code', field=models.CharField(blank=True, max_length=90)),
        migrations.AddField(model_name='domain', name='last_activation_attempt_at', field=models.DateTimeField(blank=True, null=True)),
    ]
