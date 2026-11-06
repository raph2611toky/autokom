from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('projects', '0001_initial')]
    operations = [migrations.AlterField(
        model_name='project', name='status',
        field=models.CharField(max_length=12, default='ACTIVE', choices=[
            ('ACTIVE','Actif'), ('ARCHIVED','Archivé'),
            ('DELETED','Supprimé (historique conservé)'),
        ]),
    )]
