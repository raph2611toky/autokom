from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('configurations', '0004_servicestack')]
    operations = [
        migrations.AddField(
            model_name='deploymentconfig',
            name='high_availability_requested',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='servicestack',
            name='high_availability_requested',
            field=models.BooleanField(default=False),
        ),
    ]
