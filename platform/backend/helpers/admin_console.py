"""Exécuteur interne AUTOKOM des opérations sur les administrateurs Django.

Appelé par `autokom app admin` en tant qu'utilisateur de service via stdin JSON.
Ne pas exposer en endpoint HTTP ni afficher les mots de passe.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def respond(ok: bool, message: str, *, admins=None):
    out = {'ok': bool(ok), 'message': message}
    if admins is not None:
        out['admins'] = admins
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


def perform(action, payload):
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from django.db import transaction

    from apps.assistance.auditing.models import AuditEvent

    User = get_user_model()

    def audit(action_name, user=None):
        AuditEvent.objects.create(action=action_name, target_type='Administrator',
                                  target_id=str(user.pk) if user is not None else '',
                                  metadata={'source': 'autokom-cli'})
    if User._meta.label != 'administrators.Administrator':
        return respond(False, 'Modèle administrateur inattendu : opération refusée.')
    if action == 'list':
        admins = [
            {'email': user.email, 'active': user.is_active, 'superuser': user.is_superuser}
            for user in User.objects.order_by('email').only('email', 'is_active', 'is_superuser')
        ]
        audit('admin.list')
        return respond(True, 'Liste consultée.', admins=admins)

    email = str(payload.get('email', '')).strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return respond(False, 'Adresse e-mail invalide.')

    if action == 'create':
        password = payload.get('password', '')
        if not isinstance(password, str) or len(password) < 14:
            return respond(False, 'Mot de passe trop court : 14 caractères minimum.')
        first = str(payload.get('first_name') or '')[:120]
        last = str(payload.get('last_name') or '')[:120]
        user = User(email=email, first_name=first, last_name=last,
                    is_staff=True, is_superuser=True, is_active=True)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            return respond(False, 'Mot de passe refusé : ' + '; '.join(exc.messages)[:220])
        with transaction.atomic():
            if User.objects.select_for_update().filter(email__iexact=email).exists():
                return respond(False, 'Un administrateur possède déjà cette adresse.')
            created = User.objects.create_superuser(email=email, password=password,
                                                    first_name=first, last_name=last)
            audit('admin.create', created)
        return respond(True, f'Administrateur créé : {email}')

    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user is None:
            return respond(False, 'Administrateur introuvable.')
        if action == 'reset-password':
            password = payload.get('password', '')
            if not isinstance(password, str) or len(password) < 14:
                return respond(False, 'Mot de passe trop court : 14 caractères minimum.')
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                return respond(False, 'Mot de passe refusé : ' + '; '.join(exc.messages)[:220])
            user.set_password(password)
            user.save(update_fields=['password'])
            audit('admin.reset_password', user)
            return respond(True, f'Mot de passe modifié : {email}')
        if action == 'disable':
            if user.is_active and user.is_superuser:
                # Refuser la suppression d'accès du dernier superuser actif.
                active = User.objects.filter(is_active=True, is_superuser=True).count()
                if active <= 1:
                    return respond(False, 'Dernier superutilisateur actif : désactivation refusée.')
            if not user.is_active:
                return respond(True, f'Administrateur déjà désactivé : {email}')
            user.is_active = False
            user.save(update_fields=['is_active'])
            audit('admin.disable', user)
            return respond(True, f'Administrateur désactivé : {email}')
        if action == 'enable':
            if user.is_active:
                return respond(True, f'Administrateur déjà actif : {email}')
            user.is_active = True
            user.save(update_fields=['is_active'])
            audit('admin.enable', user)
            return respond(True, f'Administrateur réactivé : {email}')
    return respond(False, 'Action non autorisée.')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1 or argv[0] not in ('create', 'list', 'reset-password', 'disable', 'enable'):
        return respond(False, 'Action admin inconnue.')
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return respond(False, 'Format de requête invalide.')
        return perform(argv[0], payload)
    except (ValueError, json.JSONDecodeError):
        return respond(False, 'Requête invalide.')
    except Exception:
        # Ne jamais afficher de traceback DB à l'utilisateur final.
        return respond(False, 'Opération Django impossible. Vérifier la connexion MySQL et le backend.')


if __name__ == '__main__':
    sys.exit(main())
