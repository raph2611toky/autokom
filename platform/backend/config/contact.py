"""Contact public de la landing AUTOKOM : SMTP uniquement, sans stockage de messages."""
import logging
import re
import ipaddress

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from helpers.responses import api_error, api_success

logger = logging.getLogger(__name__)
SUBJECTS = (
    'Question générale', 'Déploiement et intégration', 'Collaboration', 'Problème technique',
)


class ContactSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=100, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    subject = serializers.ChoiceField(choices=SUBJECTS)
    message = serializers.CharField(min_length=10, max_length=4000, trim_whitespace=True)
    # Hidden field intended to remain empty. Bot submissions are acknowledged but discarded.
    website = serializers.CharField(required=False, allow_blank=True, max_length=120, default='')

    def validate_name(self, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise serializers.ValidationError('Nom invalide.')
        return value

    def validate_message(self, value):
        if '\x00' in value:
            raise serializers.ValidationError('Message invalide.')
        return value


class ContactRateThrottle(ScopedRateThrottle):
    """Prend l'IP écrite par Nginx uniquement lorsque l'appel vient du proxy local."""
    def get_ident(self, request):
        remote = request.META.get('REMOTE_ADDR', '')
        if remote in {'127.0.0.1', '::1'}:
            forwarded = request.META.get('HTTP_X_REAL_IP', '')
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return remote or 'unknown'


@method_decorator(csrf_protect, name='dispatch')
class PublicContactAPIView(APIView):
    """Même origine + CSRF + limitation de débit ; ne divulgue jamais les secrets SMTP."""
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [ContactRateThrottle]
    throttle_scope = 'contact'

    def post(self, request):
        # Limite la mémoire et le travail de validation avant de traiter les données.
        if len(request.body) > 12_000:
            return api_error('Message trop volumineux.', status_code=413)

        serializer = ContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['website']:
            return self._uncache(api_success(message='Demande reçue.'))

        recipient = getattr(settings, 'EMAIL_SUPPORT', '').strip()
        try:
            validate_email(recipient)
            if '\r' in recipient or '\n' in recipient:
                raise DjangoValidationError('Adresse invalide.')
        except DjangoValidationError:
            logger.error('AUTOKOM contact: adresse de support non configurée ou invalide')
            return self._uncache(api_error('Le contact est momentanément indisponible.', status_code=503))

        safe_name = re.sub(r'\s+', ' ', data['name']).strip()
        subject = f'[AUTOKOM · Contact] {data["subject"]}'
        body = (f'Nom : {safe_name}\n'
                f'Adresse de réponse : {data["email"]}\n'
                f'Sujet : {data["subject"]}\n\n'
                f'{data["message"]}\n')
        try:
            email = EmailMessage(
                subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient], reply_to=[data['email']],
            )
            if email.send(fail_silently=False) != 1:
                raise RuntimeError('SMTP did not accept the message')
        except Exception as exc:
            # Never log the sender address, message body, SMTP response or credentials.
            logger.error('AUTOKOM contact: échec SMTP (%s)', type(exc).__name__)
            return self._uncache(api_error('Envoi impossible pour le moment. Réessayez plus tard.', status_code=503))
        return self._uncache(api_success(message='Votre message a été transmis au support AUTOKOM.'))

    @staticmethod
    def _uncache(response):
        response['Cache-Control'] = 'no-store, private'
        response['Pragma'] = 'no-cache'
        return response
