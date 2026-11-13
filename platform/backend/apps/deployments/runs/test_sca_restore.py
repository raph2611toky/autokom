import hashlib
import hmac
import time
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.deployments.runs.restore import image_restore_parameters, plan_restore
from apps.deployments.runs.security_feedback import normalize_sca, message
from apps.deployments.runs.security_views import verify_report_signature
from apps.deployments.runs.services import DeploymentConflict
from apps.projects.configurations.stack_views import group_release_status

REPORT = {'count':1,'secrets':0,'remaining':0,'remediated':True,'lock_sha256':'a'*64,
          'findings':[{'id':'CVE-2025-12345','package':'example-pkg','installed':'1.0.0','severity':'HIGH',
                       'fixed_versions':['1.0.1'],'applied_versions':['1.0.1'],'raw':'must not leak'}]}


class ReportTest(SimpleTestCase):
    def test_corrected_requires_a_clean_rescan_digest_and_strips_raw(self):
        safe = normalize_sca(REPORT)
        self.assertNotIn('raw', safe['findings'][0])
        self.assertIn('lockfile corrigé', message(Obj(commit_sha='b'*40,status='SUCCEEDED'),None,safe))
        self.assertIn('n’est pas confirmée', message(Obj(commit_sha='b'*40,status='FAILED'),None,safe))
        for field,value in [('secrets',1),('remaining',1),('lock_sha256','')]:
            with self.assertRaises(ValidationError):
                normalize_sca({**REPORT,field:value})

    @override_settings(AUTOKOM_RUNNER_SHARED_KEY='x'*48)
    def test_sca_proof_cannot_be_replayed_as_sast(self):
        stamp = str(int(time.time()))
        proof = hmac.new(b'x'*48, f'autokom-sca:v1:{stamp}:run:{hashlib.sha256(b"{}").hexdigest()}'.encode(),
                         hashlib.sha256).hexdigest()
        verify_report_signature('run',stamp,proof,b'{}','sca')
        with self.assertRaises(PermissionDenied):
            verify_report_signature('run',stamp,proof,b'{}','sast')


class RestoreTest(SimpleTestCase):
    def test_current_pair_requires_two_matching_runs(self):
        pair = Obj(status='SUCCEEDED', backend_run_id=1, frontend_run_id=2)
        self.assertEqual(group_release_status(pair,{1,2}),'ACTIVE')
        self.assertEqual(group_release_status(pair,{3,4}),'SUPERSEDED')
        self.assertEqual(group_release_status(pair,{1,3}),'')
        self.assertEqual(group_release_status(Obj(status='FAILED'),{1,2}),'')

    @patch('apps.deployments.runs.restore.plan_deployment')
    @patch('apps.deployments.runs.restore.check_entitlement')
    def test_old_devops_image_cannot_bypass_current_devsecops(self, entitlement, plan):
        plan.return_value = (Obj(mode='DEVSECOPS',high_availability_requested=False), {'replicas':1})
        source = Obj(environment_id=1,status='SUCCEEDED',mode_snapshot='DEVOPS',
                     configuration=Obj(high_availability_requested=False))
        with self.assertRaises(DeploymentConflict):
            plan_restore(Obj(pk=1), source, grouped=True)
        entitlement.assert_not_called()

    @patch('apps.deployments.runs.restore.Release')
    def test_restore_of_restore_uses_original_archive_id(self, release):
        release.objects.get.return_value.image_digest = 'sha256:'+'1'*64
        original = Obj(pk='first',environment_id=1,rollback_source_id=None)
        previous = Obj(pk='second',environment_id=1,rollback_source_id='first',rollback_source=original)
        run = Obj(environment_id=1,environment=Obj(pk=1),rollback_source=previous,
                  resources_snapshot={'restore_expected_current':'latest'})
        self.assertEqual(image_restore_parameters(run), {'source_run':'first','expected_current':'latest',
                                                          'image_digest':'sha256:'+'1'*64})
        original.environment_id = 2
        with self.assertRaises(DeploymentConflict):
            image_restore_parameters(run)


class RestoreWorkerTest(SimpleTestCase):
    def exercise(self, failure=None):
        from apps.deployments.runs.restore import process_restore
        run = Obj(pk='restore', status='QUEUED', cancel_requested=False, environment_id=7, environment=Obj(pk=7),
                  rollback_source=Obj(pk='old'), resources_snapshot={'replicas':1,'restore_expected_current':'active'},
                  save=Mock(), refresh_from_db=Mock(), stages=Mock())
        with patch('apps.deployments.runs.restore.transaction.atomic'), \
             patch('apps.deployments.runs.restore.DeploymentRun') as model, \
             patch('apps.deployments.runs.restore.actor_can_restore',return_value=True), \
             patch('apps.deployments.runs.restore.plan_restore',return_value={'replicas':1}), \
             patch('apps.deployments.runs.restore.current_release',return_value=Obj(source_deployment_id='active')), \
             patch('apps.deployments.runs.restore.image_restore_parameters',return_value={'source_run':'old'}), \
             patch('apps.deployments.runs.worker._release_docker_and_proxy',side_effect=failure) as candidate, \
             patch('apps.deployments.runs.worker.add_log'), \
             patch('apps.deployments.runs.worker.register_release') as register, \
             patch('apps.deployments.runs.autokom_socket.request_control',return_value={'image_digest':'sha256:'+'b'*64}) as broker:
            model.objects.select_for_update.return_value.filter.return_value.order_by.return_value.first.return_value = run
            self.assertTrue(process_restore())
            return candidate,register,broker,model.objects.filter.return_value.update.call_args.kwargs

    def test_publishes_after_candidate_health(self):
        candidate, register, broker, final = self.exercise()
        self.assertEqual(candidate.call_args.kwargs['restore'], {'source_run':'old'})
        self.assertEqual(broker.call_args.args[0], 'runtime.commit')
        self.assertEqual(register.call_args.kwargs['image_digest'], 'sha256:'+'b'*64)
        self.assertEqual(final['status'], 'SUCCEEDED')

    def test_candidate_failure_preserves_previous_release(self):
        _, register, broker, final = self.exercise(RuntimeError('TLS failed'))
        self.assertEqual(broker.call_args.args[0], 'runtime.rollback')
        register.assert_not_called()
        self.assertEqual(final['status'], 'FAILED')
