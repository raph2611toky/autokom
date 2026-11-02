import uuid
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from apps.accounts.admin.managers import AdministratorManager

class Administrator(AbstractBaseUser, PermissionsMixin):
    """Only Django-native authentication principal; never created via public APIs."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    is_staff = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)
    objects = AdministratorManager()
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    class Meta:
        db_table = 'administrators_administrator'
        verbose_name = 'Administrateur système'
        verbose_name_plural = 'Administrateurs système'
