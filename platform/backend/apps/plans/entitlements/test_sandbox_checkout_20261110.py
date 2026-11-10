"""À exécuter sur la base de test MySQL, jamais contre la base de production."""
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.accounts.user.models import User
from apps.organizations.models import Organization, OrganizationMembership
from apps.projects.project.models import Project
from apps.projects.environments.models import Environment
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.configurations.services import check_entitlement
from apps.plans.catalog.models import DeploymentModeOffer, ResourcePlan
from apps.plans.entitlements.models import ApplicationEntitlement, HighAvailabilityOption, SandboxCheckout
from apps.plans.entitlements.sandbox import checkout, enabled_for
from apps.plans.entitlements.views import SandboxCheckoutAPIView
from apps.deployments.runs.services import effective_replicas


@override_settings(AUTOKOM_BILLING_SANDBOX_ENABLED=True,
                   AUTOKOM_BILLING_SANDBOX_TEST_EMAILS=frozenset({'payer@example.com'}))
class SandboxCheckoutTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email='payer@example.com', full_name='Test payer', password='a-long-strong-password')
        self.owner.is_email_verified = True
        self.owner.save(update_fields=['is_email_verified'])
        self.other = User.objects.create_user(email='other@example.com', full_name='Other', password='a-long-strong-password')
        self.other.is_email_verified = True
        self.other.save(update_fields=['is_email_verified'])
        self.org = Organization.objects.create(owner=self.owner, name='Organisation test', slug='test-payer-org')
        OrganizationMembership.objects.create(organization=self.org, user=self.owner, role='OWNER')
        self.project = Project.objects.create(organization=self.org, created_by=self.owner, name='Portfolio', slug='portfolio')
        self.env = Environment.objects.create(project=self.project, name='Production', slug='production')
        self.offer = DeploymentModeOffer.objects.create(code='DEVOPS', name='DevOps', is_premium=False, is_default=True)
        self.starter = ResourcePlan.objects.create(code='starter', name='Starter', cpu_millicores=250,
            memory_mb=512, storage_mb=1024, is_default=True, is_premium=False, max_instances=1)
        self.ent = ApplicationEntitlement.objects.create(environment=self.env, mode_offer=self.offer, resource_plan=self.starter,
            status='ACTIVE', grant_source='SYSTEM', activated_at=timezone.now())

    @override_settings(AUTOKOM_BILLING_SANDBOX_TEST_EMAILS=frozenset({'payer@example.com', 'other@example.com'}))
    def test_allowlisted_user_cannot_subscribe_to_other_organization(self):
        with self.assertRaises(PermissionDenied):
            checkout(self.other, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))

    def test_disabled_by_default_for_other_account(self):
        self.assertFalse(enabled_for(self.other))
        with self.assertRaises(PermissionDenied):
            checkout(self.other, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))

    @override_settings(AUTOKOM_BILLING_SANDBOX_ENABLED=False)
    def test_disabled_flag_denies_even_allowlisted_account(self):
        with self.assertRaises(PermissionDenied):
            checkout(self.owner, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))

    def test_pro_subscription_grants_two_instances_and_option_without_forcing_ha(self):
        receipt = checkout(self.owner, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))
        self.ent.refresh_from_db()
        self.assertEqual(receipt.status, 'ACCEPTED')
        self.assertEqual(self.ent.grant_source, 'TEST')
        self.assertEqual(self.ent.resource_plan.max_instances, 2)
        self.assertEqual(self.ent.mode_offer.code, 'DEVSECOPS')
        self.assertEqual(self.ent.sandbox_checkout_id, receipt.id)
        option = HighAvailabilityOption.objects.get(environment=self.env)
        self.assertEqual(option.status, 'ACTIVE')
        self.assertEqual(option.desired_replicas, 2)
        cfg = DeploymentConfig(environment=self.env, mode='DEVOPS', build_method='STATIC',
                               high_availability_requested=False)
        self.assertEqual(check_entitlement(cfg).max_instances, 2)
        self.assertEqual(effective_replicas(self.env, self.ent.resource_plan, cfg), 1)
        cfg.high_availability_requested = True
        self.assertEqual(check_entitlement(cfg).max_instances, 2)

    def test_mode_prod_blocks_existing_test_entitlement_without_deleting_receipt(self):
        receipt = checkout(self.owner, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))
        cfg = DeploymentConfig(environment=self.env, mode='DEVOPS', build_method='STATIC',
                               high_availability_requested=True)
        self.assertEqual(check_entitlement(cfg).max_instances, 2)
        with override_settings(AUTOKOM_BILLING_SANDBOX_ENABLED=False):
            with self.assertRaises(PermissionDenied):
                check_entitlement(cfg)
            from apps.plans.entitlements.sandbox import receipt_summary
            self.assertFalse(receipt_summary(receipt)['is_valid'])
        self.assertTrue(SandboxCheckout.objects.filter(pk=receipt.id).exists())

    def test_essential_grants_one_instance_and_no_ha(self):
        checkout(self.owner, self.project, self.env, 'essential', 'annual', str(uuid.uuid4()))
        self.ent.refresh_from_db()
        self.assertEqual(self.ent.resource_plan.max_instances, 1)
        self.assertFalse(HighAvailabilityOption.objects.filter(environment=self.env).exists())

    def test_idempotency_and_reuse_restrictions(self):
        key = str(uuid.uuid4())
        a = checkout(self.owner, self.project, self.env, 'pro', 'monthly', key)
        b = checkout(self.owner, self.project, self.env, 'pro', 'monthly', key)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(SandboxCheckout.objects.count(), 1)
        with self.assertRaises(ValidationError):
            checkout(self.owner, self.project, self.env, 'essential', 'monthly', key)

    def test_expired_receipt_does_not_grant_rights(self):
        receipt = checkout(self.owner, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))
        SandboxCheckout.objects.filter(pk=receipt.pk).update(expires_at=timezone.now()-timedelta(seconds=3))
        self.ent.refresh_from_db()
        cfg = DeploymentConfig(environment=self.env, mode='DEVOPS', build_method='STATIC', high_availability_requested=True)
        with self.assertRaises(PermissionDenied):
            check_entitlement(cfg)

    def test_cannot_replace_real_admin_entitlement(self):
        self.ent.grant_source = 'ADMIN'
        self.ent.status = 'ACTIVE'
        self.ent.save(update_fields=['grant_source','status'])
        with self.assertRaises(PermissionDenied):
            checkout(self.owner, self.project, self.env, 'pro', 'monthly', str(uuid.uuid4()))

    def test_legacy_environment_payment_cannot_bypass_account_subscription(self):
        factory = APIRequestFactory()
        url = f'/api/projects/{self.project.pk}/environments/{self.env.pk}/billing/sandbox/checkout/'
        payload = {'plan':'pro', 'period':'monthly', 'idempotency_key':str(uuid.uuid4())}
        req = factory.post(url, payload, format='json')
        force_authenticate(req, user=self.owner)
        res = SandboxCheckoutAPIView.as_view()(req, project_id=self.project.pk, environment_id=self.env.pk)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(SandboxCheckout.objects.exists())
