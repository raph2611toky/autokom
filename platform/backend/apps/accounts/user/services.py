"""Account business services. User identity is disjoint from the Django admin principal."""
import logging
import secrets
from urllib.parse import urlencode
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from helpers.security import random_token, token_digest
from helpers.services.emails import send_template_email
from apps.organizations.models import Organization, OrganizationMembership
from apps.accounts.user.models import User, UserPreference, EmailVerification, PasswordResetRequest

logger = logging.getLogger(__name__)

def _mail_token(user, *, kind, rotate=True):
    token = random_token()
    now = timezone.now()
    if kind == 'verify':
        if rotate:
            EmailVerification.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
        EmailVerification.objects.create(user=user, token_hash=token_digest(token),
            expires_at=now + settings.EMAIL_VERIFICATION_LIFETIME)
        url = settings.FRONTEND_URL + '/verify-email?' + urlencode({'token': token})
        template, subject = 'emails/verify.html', 'Confirmez votre adresse AUTOKOM'
    else:
        if rotate:
            PasswordResetRequest.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
        PasswordResetRequest.objects.create(user=user, token_hash=token_digest(token),
            expires_at=now + settings.PASSWORD_RESET_LIFETIME)
        url = settings.FRONTEND_URL + '/reset-password?' + urlencode({'token': token})
        template, subject = 'emails/reset.html', 'Réinitialisez votre mot de passe AUTOKOM'
    try:
        send_template_email(subject=subject, to=user.email, template=template, context={'url': url})
        return True
    except Exception:
        logger.exception('Échec de la transmission du lien %s au compte %s', kind, user.pk)
        return False

@transaction.atomic
def register_client(*, email, full_name, password):
    user = User.objects.create_user(email=email, full_name=full_name, password=password)
    UserPreference.objects.create(user=user)
    # Space personal auto-créé. Slug: UUID (stable, unique, non exposant l'email).
    organization = Organization.objects.create(owner=user, name='Espace personnel', slug='personal-' + user.pk.hex, kind='PERSONAL')
    OrganizationMembership.objects.create(organization=organization, user=user, role='OWNER')
    return user

def send_verification(user):
    return _mail_token(user, kind='verify')

def send_reset(user):
    return _mail_token(user, kind='reset')
