"""Identités GitHub vérifiées, états PKCE et tickets de connexion à usage unique."""
import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='ClientGitHubIdentity',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('github_user_id', models.PositiveBigIntegerField(unique=True)),
                ('github_login', models.CharField(max_length=160)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                    related_name='github_identity', to='accounts.user')),
            ],
            options={'db_table': 'accounts_clientgithubidentity'},
        ),
        migrations.CreateModel(
            name='ClientGitHubOAuthState',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('state_hash', models.CharField(max_length=64, unique=True)),
                ('browser_binding_hash', models.CharField(max_length=64)),
                ('intent', models.CharField(max_length=12)),
                ('pkce_verifier_ciphertext', models.TextField()),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('link_user', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE, related_name='github_auth_states', to='accounts.user')),
            ],
            options={'db_table': 'accounts_clientgithubstate'},
        ),
        migrations.CreateModel(
            name='ClientGitHubLoginTicket',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('browser_binding_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='github_login_tickets', to='accounts.user')),
            ],
            options={'db_table': 'accounts_clientgithubticket'},
        ),
    ]
