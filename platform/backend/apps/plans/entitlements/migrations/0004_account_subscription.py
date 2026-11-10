from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('plan_entitlements', '0003_sandbox_subscription')]
    operations = [
        migrations.AlterField(
            model_name='sandboxcheckout', name='environment',
            field=models.ForeignKey(to='environments.environment', on_delete=django.db.models.deletion.PROTECT,
                                    null=True, blank=True, related_name='sandbox_checkouts'),
        ),
        migrations.CreateModel(
            name='AccountSubscription',
            fields=[
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('receipt', models.ForeignKey(to='plan_entitlements.sandboxcheckout',
                     related_name='account_subscriptions', on_delete=django.db.models.deletion.PROTECT)),
                ('user', models.OneToOneField(to='accounts.user', primary_key=True, serialize=False,
                     related_name='subscription', on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={'db_table': 'plans_accountsubscription'},
        ),
    ]
