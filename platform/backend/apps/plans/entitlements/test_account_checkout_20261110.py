"""Account billing integration: run only through Django's dedicated test DB."""
import uuid
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.user.models import User
from apps.organizations.models import Organization
from apps.projects.project.models import Project
from apps.projects.environments.models import Environment
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.configurations.services import check_entitlement
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.plans.entitlements.models import (ApplicationEntitlement, AccountSubscription,
                                             HighAvailabilityOption, SandboxCheckout)
from apps.plans.entitlements.account import checkout_account, account_status
from apps.projects.project.services import free_entitlement


@override_settings(AUTOKOM_BILLING_SANDBOX_ENABLED=True,
                   AUTOKOM_BILLING_SANDBOX_TEST_EMAILS=frozenset({'payer@example.com'}))
class AccountCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='payer@example.com', full_name='Payer', password='strong-long-password')
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])
        self.other = User.objects.create_user(email='other@example.com', full_name='Other', password='strong-long-password')
        self.other.is_email_verified = True
        self.other.save(update_fields=['is_email_verified'])
        self.org = Organization.objects.create(owner=self.user, name='Personal', slug='billing-account-personal')
        self.project = Project.objects.create(organization=self.org, created_by=self.user, name='Portfolio', slug='portfolio')
        self.env = Environment.objects.create(project=self.project, name='Production', slug='production')
        DeploymentModeOffer.objects.create(code='DEVOPS', name='DevOps', is_premium=False, is_default=True)
        DeploymentModeOffer.objects.create(code='DEVSECOPS', name='DevSecOps', is_premium=True)
        ResourcePlan.objects.create(code='starter', name='Starter', cpu_millicores=250, memory_mb=256,
                                    storage_mb=1024, is_default=True, is_premium=False, max_instances=1)
        free_entitlement(self.env)

    def test_pro_account_checkout_grants_existing_and_future_owned_environments(self):
        receipt = checkout_account(self.user, 'pro', 'monthly', uuid.uuid4())
        self.assertIsNone(receipt.environment_id)
        self.assertEqual(AccountSubscription.objects.get(user=self.user).receipt_id, receipt.pk)
        self.assertEqual(account_status(self.user)['plan'], 'pro')
        current = ApplicationEntitlement.objects.get(environment=self.env)
        self.assertEqual(current.resource_plan.max_instances, 2)
        self.assertTrue(HighAvailabilityOption.objects.filter(environment=self.env, status='ACTIVE').exists())
        cfg = DeploymentConfig(environment=self.env, mode='DEVOPS', build_method='STATIC',
                               high_availability_requested=True)
        self.assertEqual(check_entitlement(cfg).max_instances, 2)
        future = Environment.objects.create(project=self.project, name='Preproduction', slug='staging')
        free_entitlement(future)
        self.assertEqual(future.entitlement.resource_plan.max_instances, 2)
        self.assertTrue(HighAvailabilityOption.objects.filter(environment=future, status='ACTIVE').exists())

    def test_payment_request_is_idempotent(self):
        key = uuid.uuid4()
        first = checkout_account(self.user, 'essential', 'monthly', key)
        self.assertEqual(checkout_account(self.user, 'essential', 'monthly', key).pk, first.pk)
        self.assertEqual(SandboxCheckout.objects.count(), 1)
        with self.assertRaises(ValidationError):
            checkout_account(self.user, 'pro', 'monthly', key)

    def test_cannot_buy_for_other_account(self):
        with self.assertRaises(PermissionDenied):
            checkout_account(self.other, 'pro', 'monthly', uuid.uuid4())
        self.assertFalse(AccountSubscription.objects.filter(user=self.other).exists())

    def test_production_mode_disables_demo_rights(self):
        checkout_account(self.user, 'pro', 'monthly', uuid.uuid4())
        with override_settings(AUTOKOM_BILLING_SANDBOX_ENABLED=False):
            self.assertEqual(account_status(self.user)['plan'], 'free')
            with self.assertRaises(PermissionDenied):
                check_entitlement(DeploymentConfig(environment=self.env, mode='DEVOPS', build_method='STATIC'))

    def test_existing_admin_subscription_is_not_overwritten(self):
        ent = self.env.entitlement
        ent.grant_source = 'ADMIN'
        ent.save(update_fields=['grant_source'])
        checkout_account(self.user, 'pro', 'monthly', uuid.uuid4())
        ent.refresh_from_db()
        self.assertEqual(ent.grant_source, 'ADMIN')

    def test_account_checkout_api_changes_profile_without_project_parameter(self):
        from apps.plans.entitlements.views import AccountCheckoutAPIView, AccountSubscriptionAPIView
        factory = APIRequestFactory()
        request = factory.post('/api/billing/account/checkout/', {
            'plan': 'pro', 'period': 'monthly', 'idempotency_key': str(uuid.uuid4()),
        }, format='json')
        force_authenticate(request, user=self.user)
        result = AccountCheckoutAPIView.as_view()(request)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['data']['amount_charged_ariary'], 0)
        self.assertFalse(result.data['data']['payment_real'])
        request = factory.get('/api/billing/account/')
        force_authenticate(request, user=self.user)
        result = AccountSubscriptionAPIView.as_view()(request)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['subscription']['plan'], 'pro')
