"""Endpoints client de paiement simulé ; aucun secret de carte, aucun débit."""
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.plans.entitlements.models import SandboxCheckout
from apps.plans.entitlements.account import account_status, checkout_account
from apps.plans.entitlements.sandbox import TEST_PLANS, checkout, enabled_for, receipt_summary, require_billing_owner
from helpers.errors import handle_error
from helpers.responses import api_success


class SandboxAvailabilityAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request):
        enabled = enabled_for(request.user)
        return api_success({'enabled': enabled, 'sandbox': True, 'real_payment': False,
            'offers': {code: {'monthly': item['monthly'], 'annual': item['annual'],
                              'max_instances': item['instances']} for code, item in TEST_PLANS.items()} if enabled else {},
            }, 'Configuration de la simulation de paiement.')


class SandboxCheckoutAPIView(APIView):
    """Legacy endpoint retained for clients; payments now belong to accounts.

    A user must not bypass their account subscription by buying a different
    plan for a single environment via an old API route.
    """
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
            ent = getattr(env, 'entitlement', None)
            active = (ent.sandbox_checkout if ent and ent.grant_source == 'TEST' and ent.sandbox_checkout_id else None)
            return api_success({'enabled': enabled_for(request.user),
                'receipt': receipt_summary(active) if active else None}, 'Abonnement de test.')
        except Exception as exc:
            return handle_error(exc, 'sandbox_subscription')


class SandboxAccountSubscriptionsAPIView(APIView):
    permission_classes = [IsClientUser]
    def get(self, request):
        records = SandboxCheckout.objects.filter(created_by=request.user).select_related('resource_plan').order_by('-created_at')[:50]
        return api_success({'enabled': enabled_for(request.user), 'sandbox': True,
            'subscriptions': [dict(receipt_summary(row), environment_id=str(row.environment_id)) for row in records]},
            'Historique de vos abonnements simulés.')


class AccountSubscriptionAPIView(APIView):
    """Account-authoritative plan; no client-supplied project or entitlement ID."""
    permission_classes = [IsClientUser]

    def get(self, request):
        return api_success({'subscription': account_status(request.user)}, 'Abonnement du compte.')


class AccountCheckoutAPIView(APIView):
    """Explicitly a simulated no-charge checkout, not a real financial payment."""
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'sandbox_checkout'

    def post(self, request):
        try:
            if not isinstance(request.data, dict):
                raise ValidationError('Corps JSON attendu.')
            checkout_account(request.user, request.data.get('plan'), request.data.get('period'),
                             request.data.get('idempotency_key'))
            return api_success({'subscription': account_status(request.user),
                                'amount_charged_ariary': 0, 'payment_real': False},
                               'Souscription de démonstration enregistrée. Aucun débit réel.', 201)
        except Exception as exc:
            return handle_error(exc, 'account_checkout')
