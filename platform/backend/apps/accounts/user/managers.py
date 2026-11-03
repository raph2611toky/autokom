from django.db import models

class ClientUserManager(models.Manager):
    def create_user(self, *, email, password, full_name, **extra_fields):
        email = str(email or '').strip().lower()
        if not email or not password:
            raise ValueError('E-mail et mot de passe obligatoires.')
        user = self.model(email=email, full_name=full_name.strip(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
