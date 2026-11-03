from django.contrib.auth.base_user import BaseUserManager

class AdministratorManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'adresse e-mail est obligatoire.")
        email = self.normalize_email(email).strip().lower()
        extra_fields.setdefault('is_staff', True)
        if extra_fields['is_staff'] is not True:
            raise ValueError('Les comptes administrateurs ont un accès staff.')
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Un superutilisateur doit avoir is_superuser=True.')
        return self.create_user(email, password, **extra_fields)
