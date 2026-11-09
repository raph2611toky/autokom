import uuid
import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('configurations', '0003_deploymentconfig_mode_deploymentconfig_resource_plan_and_more'),
                    ('projects', '0002_project_deleted'), ('environments', '0002_initial')]
    operations = [migrations.CreateModel(
        name='ServiceStack',
        fields=[('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('auto_deploy', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='service_stack', to='projects.project')),
                ('frontend_environment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='frontend_stacks', to='environments.environment')),
                ('backend_environment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='backend_stacks', to='environments.environment'))],
        options={'db_table': 'configurations_servicestack'},
    )]
