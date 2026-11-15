from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('environments', '0002_initial')]
    operations = [
        migrations.AddField(
            model_name='environment',
            name='wireguard_only',
            field=models.BooleanField(default=False),
        ),
    ]
