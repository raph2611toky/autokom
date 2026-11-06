"""Contrôles statiques de sécurité du contrat Django. Ne remplacent pas les tests DB."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / 'platform/backend/apps/deployments/runs/views.py'
SERVICES = ROOT / 'platform/backend/apps/deployments/runs/services.py'
URLS = ROOT / 'platform/backend/apps/deployments/runs/urls.py'


class CustomerRunsContractTests(unittest.TestCase):
    def test_views_remain_parsable(self):
        for file in (VIEWS, SERVICES, URLS):
            ast.parse(file.read_text(encoding='utf-8'), filename=str(file))

    def test_launch_is_protected_and_does_not_call_jenkins_directly(self):
        source = VIEWS.read_text(encoding='utf-8')
        self.assertIn('class DeploymentEnqueueAPIView', source)
        launch = source.split('class DeploymentEnqueueAPIView')[1].split('class DeploymentListAPIView')[0]
        self.assertIn('permission_classes = [IsClientUser]', launch)
        self.assertIn("request.headers.get('Idempotency-Key')", launch)
        self.assertIn('queue_manual(request.user', launch)
        self.assertNotIn('JenkinsClient(', launch)

    def test_runs_are_filtered_by_environment_and_paged(self):
        source = VIEWS.read_text(encoding='utf-8')
        listing = source.split('class DeploymentListAPIView')[1].split('class DeploymentDetailAPIView')[0]
        self.assertIn('get_environment(request.user', listing)
        self.assertIn('DeploymentRun.objects.filter(environment=env)', listing)
        self.assertIn("order_by('-created_at')", listing)
        self.assertIn("int_param(request, 'page_size', default=20, maximum=50)", listing)
        self.assertIn("'count': qs.count()", listing)
        self.assertIn('qs[(page-1)*size:page*size]', listing)

    def test_manual_run_is_idempotent_and_refuses_simultaneous_run(self):
        text = SERVICES.read_text(encoding='utf-8')
        body = text.split('def queue_manual(')[1].split('def queue_verified_push(')[0]
        self.assertIn('get_environment(user, project_id, environment_id, write=True)', body)
        self.assertIn('idempotency_key=idem', body)
        self.assertIn('select_for_update()', body)
        self.assertIn("status__in=['QUEUED','RUNNING']", body)
        self.assertIn('ensure_runner_ready()', body)

    def test_existing_client_routes_cover_publishing_and_history(self):
        text = URLS.read_text(encoding='utf-8')
        for fragment in ('deployments/preview/', 'deployments/launch/',
                         'deployments/<uuid:run_id>/', 'deployments/<uuid:run_id>/logs/',
                         "name='deployment-list'"):
            self.assertIn(fragment, text)


if __name__ == '__main__':
    unittest.main()
