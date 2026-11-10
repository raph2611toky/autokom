"""Endpoints client de paiement simulé ; aucun secret de carte, aucun débit."""
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.plans.entitlements.models import SandboxCheckout
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
    parser_classes = [JSONParser]
    permission_classes = [IsClientUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'sandbox_checkout'
    def post(self, request, project_id, environment_id):
        try:
            project, env = get_environment(request.user, project_id, environment_id, write=True)
            if not isinstance(request.data, dict):
                raise ValidationError('Corps JSON attendu.')
            receipt = checkout(request.user, project, env, request.data.get('plan'),
                               request.data.get('period'), request.data.get('idempotency_key'))
            return api_success({'receipt': receipt_summary(receipt)},
                'Paiement simulé accepté. Aucun argent débité : droits TEST activés.', 201)
        except Exception as exc:
            return handle_error(exc, 'sandbox_checkout')


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
