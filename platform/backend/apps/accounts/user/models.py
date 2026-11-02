import uuid
from django.contrib.auth.hashers import make_password, check_password
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from apps.accounts.user.managers import ClientUserManager

class User(models.Model):
    """Client applicatif; distinct du compte Django administrateur."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=180)
    password = models.CharField(max_length=128)
    avatar = models.ImageField(upload_to='users/avatars/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    token_version = models.PositiveBigIntegerField(default=0)
    objects = ClientUserManager()

    def set_password(self, value):
        self.password = make_password(value)

    def check_password(self, value):
        return check_password(value, self.password)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return self.email

    class Meta:
        db_table = 'accounts_user'
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_lower_client_email')]

class EmailVerification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='email_verifications')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'EmailVerification : {self.pk}'

    class Meta:
        db_table = 'accounts_emailverification'

class PasswordResetRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='password_resets')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'PasswordResetRequest : {self.pk}'

    class Meta:
        db_table = 'accounts_passwordresetrequest'

class UserSession(models.Model):
    """Session de renouvellement JWT client, révoquée à chaque rotation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='sessions')
    jti_hash = models.CharField(max_length=64, unique=True)
    token_family = models.UUIDField(default=uuid.uuid4, db_index=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    @property
    def is_valid(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    class Meta:
        db_table = 'accounts_usersession'
        indexes = [models.Index(fields=['user', 'revoked_at', 'expires_at'])]

    def __str__(self):
        return f'UserSession : {self.pk}'

class MFADevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='mfa_devices')
    method = models.CharField(max_length=25, default='TOTP')
    name = models.CharField(max_length=150)
    secret_ref = models.CharField(max_length=255, blank=True)
    is_confirmed = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'MFADevice : {self.name}'

    class Meta:
        db_table = 'accounts_mfadevice'

class UserPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='preferences')
    language = models.CharField(max_length=12, default='fr')
    timezone = models.CharField(max_length=64, default='Indian/Antananarivo')
    theme = models.CharField(max_length=12, default='system')
    settings = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'UserPreference : {self.pk}'

    class Meta:
        db_table = 'accounts_userpreference'
