import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory
from apps.deployments.runs import runner_dast as worker
from apps.deployments.runs.security_views import RunnerDASTAPIView


class RunnerDASTTests(SimpleTestCase):
    @override_settings(AUTOKOM_RUNNER_SHARED_KEY='x'*48)
    def test_dast_signature_is_distinct_from_sast_and_endpoint_returns_status(self):
        rid, stamp = uuid4(), str(int(time.time()))
        def send(kind):
            digest = hashlib.sha256(b'{}').hexdigest()
            proof = hmac.new(b'x'*48, f'autokom-{kind}:v1:{stamp}:{rid}:{digest}'.encode(),
                             hashlib.sha256).hexdigest()
            request = APIRequestFactory().post('/dast/', '{}', content_type='application/json',
                     HTTP_X_AUTOKOM_RUNNER_TIME=stamp, HTTP_X_AUTOKOM_RUNNER_SIGNATURE=proof)
            return RunnerDASTAPIView.as_view()(request, run_id=rid)
        with patch.object(worker,'runner_dast_status',return_value=worker.REQUESTED) as state:
            self.assertEqual(send('sast').status_code,403)
            state.assert_not_called()
            response = send('dast')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data['data']['status'],worker.REQUESTED)
            state.assert_called_once_with(rid)

    def test_preflight_only_gets_port_and_budget_not_secrets_or_host(self):
        for http, expected in ((200, worker.PASSED),(0, worker.DEFERRED),(503, worker.DEFERRED)):
            run = SimpleNamespace(pk=uuid4())
            stage = Mock(pk='stage', deployment=run, external_stage_id=worker.REQUESTED)
            manifest = {'build':{'port':8080,'healthcheck':'/'},
                        'resources':{'memory_mb':256,'cpu_millicores':200}, 'variables':{'do_not_send':'value'}}
            with patch.object(worker.transaction,'atomic'), patch.object(worker,'PipelineStageRun') as stages, \
                 patch('apps.deployments.runs.runner_manifest.fetch_runner_manifest',return_value=manifest) as fetch, \
                 patch('apps.deployments.runs.autokom_socket.request_control',return_value={'http':http}) as broker:
                stages.objects.select_for_update.return_value.filter.return_value.order_by.return_value.first.return_value = stage
                self.assertTrue(worker.process_dast_previews())
                fetch.assert_called_once_with(run.pk)
                params = broker.call_args.args[1]
                self.assertEqual(set(params),{'run_id','internal_port','healthcheck','memory_mb','cpu_millicores'})
                result = stages.objects.filter.return_value.update.call_args.kwargs
                self.assertEqual(result['external_stage_id'],expected)
