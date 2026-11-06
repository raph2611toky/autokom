from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('domains', '0002_initial')]
    operations = [
        migrations.AddField(model_name='domain', name='is_primary', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='domain', name='is_disabled', field=models.BooleanField(default=False)),
        migrations.AddConstraint(model_name='domain', constraint=models.UniqueConstraint(
            fields=['environment'], condition=models.Q(is_primary=True),
            name='autokom_one_primary_domain_per_env')),
    ]
