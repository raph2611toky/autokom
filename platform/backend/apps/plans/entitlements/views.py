"""Client billing APIs. MVP launch offer is free and never simulates charging."""
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.plans.entitlements.models import SandboxCheckout
from apps.plans.entitlements.account import account_status, checkout_account
from apps.plans.entitlements.sandbox import TEST_PLANS, enabled_for, receipt_summary, require_billing_owner
from apps.plans.entitlements.mvp import enabled as mvp_enabled, status_for_account
from helpers.errors import handle_error
from helpers.responses import api_success

class SandboxAvailabilityAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request):
        if mvp_enabled() and not enabled_for(request.user):
            return api_success({'enabled': False, 'sandbox': False, 'real_payment': False,
                                'offers': {}}, 'Offre gratuite de lancement disponible.')
        enabled = enabled_for(request.user)
        return api_success({'enabled': enabled, 'sandbox': True, 'real_payment': False,
            'offers': {code: {'monthly': item['monthly'], 'annual': item['annual'],
                              'max_instances': item['instances']} for code, item in TEST_PLANS.items()} if enabled else {},
            }, 'Configuration de la simulation de paiement.')

class SandboxCheckoutAPIView(APIView):
    """Legacy endpoint retained; per-environment checkout must not bypass account rights."""
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'sandbox_checkout'
    def post(self, request, project_id, environment_id):
        return handle_error(ValidationError(
            'Le parcours de souscription a été déplacé dans Mon compte.'),
            'legacy_environment_checkout')

class SandboxSubscriptionAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request, project_id, environment_id):
        try:
            project, env = get_environment(request.user, project_id, environment_id)
            require_billing_owner(request.user, project)
            if mvp_enabled() and not enabled_for(request.user):
                return api_success({'enabled': False, 'receipt': None}, 'Offre gratuite de lancement.')
            ent = getattr(env, 'entitlement', None)
            active = (ent.sandbox_checkout if ent and ent.grant_source == 'TEST' and ent.sandbox_checkout_id else None)
            return api_success({'enabled': enabled_for(request.user),
                'receipt': receipt_summary(active) if active else None}, 'Abonnement de test.')
        except Exception as exc:
            return handle_error(exc, 'sandbox_subscription')

class SandboxAccountSubscriptionsAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request):
        if mvp_enabled() and not enabled_for(request.user):
            return api_success({'enabled': False, 'sandbox': False,
                                'subscriptions': []}, 'Offre de lancement active.')
        records = SandboxCheckout.objects.filter(created_by=request.user).select_related('resource_plan').order_by('-created_at')[:50]
        return api_success({'enabled': enabled_for(request.user), 'sandbox': True,
            'subscriptions': [dict(receipt_summary(row), environment_id=str(row.environment_id)) for row in records]},
            'Historique de vos abonnements simulés.')

class AccountSubscriptionAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request):
        value = account_status(request.user)
        if mvp_enabled() and not value['active']:
            # 'pro' is a frontend compatibility code for the HA toggle only.
            # The authoritative backend grant is the non-premium, zero-charge
            # launch plan; no AccountSubscription or paid receipt is created.
            value = {'plan': 'pro', 'plan_name': 'Offre gratuite de lancement',
                     'active': True, 'max_instances': 2, 'ha_available': True,
                     'period': None, 'expires_at': None, 'receipt': None,
                     'can_subscribe': value['can_subscribe'], 'payment_available': False,
                     'launch_offer': status_for_account(request.user)}
        return api_success({'subscription': value}, 'Abonnement du compte.')

class AccountCheckoutAPIView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'sandbox_checkout'
    def post(self, request):
        try:
            if mvp_enabled() and not enabled_for(request.user):
                raise ValidationError('Aucun paiement requis : l’offre de lancement est activée automatiquement au déploiement.')
            if not isinstance(request.data, dict):
                raise ValidationError('Corps JSON attendu.')
            checkout_account(request.user, request.data.get('plan'), request.data.get('period'),
                             request.data.get('idempotency_key'))
            return api_success({'subscription': account_status(request.user),
                                'amount_charged_ariary': 0, 'payment_real': False},
                               'Souscription de démonstration enregistrée. Aucun débit réel.', 201)
        except Exception as exc:
            return handle_error(exc, 'account_checkout')
