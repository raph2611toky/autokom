"""Pure, self-contained webhook state tests: no MySQL or GitHub network required."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / 'platform/backend/apps/deployments/runs/services.py'
RECONCILE = ROOT / 'platform/backend/apps/deployments/runs/push_reconcile.py'
WORKER = ROOT / 'platform/backend/apps/deployments/runs/worker.py'
WEBHOOK = ROOT / 'platform/backend/apps/projects/git/views.py'
APP_MANAGER = ROOT / 'scripts/lib/autokom_app_manager.py'


def standalone_function(file, name):
    tree = ast.parse(file.read_text(encoding='utf-8'))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    fn.decorator_list = []
    fn.body = [s for s in fn.body if not isinstance(s, ast.ImportFrom)]
    return compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(file), 'exec')


class QS(list):
    def __init__(self, items=()):
        super().__init__(items)
    def select_for_update(self): return self
    def order_by(self, *args): return self
    def first(self): return self[0] if self else None
    def exists(self): return bool(self)
    def exclude(self, **kwargs): return self
    def get(self, **kwargs): return self[0]


class Database:
    def __init__(self):
        self.event = SimpleNamespace(
            pk='event-id', id='event-id', status='RECEIVED', signature_verified=True,
            event_type='push', commit_sha='9cbbc61' + 'a'*33,
            branch='main', repository_external_id='123',
            repository_full_name='raph2611toky/jirama',
            connection=SimpleNamespace(id='connection-id'),
            received_at=datetime.now(timezone.utc),
            processed_at=None, error_message='',
        )
        self.event.save = lambda update_fields: None
        self.project = SimpleNamespace(id='project', git_connection=self.event.connection)
        self.env = SimpleNamespace(id='env', pk='env')
        self.cfg = SimpleNamespace(auto_deploy=True, auto_deploy_branch='main', branch='main')
        self.created = []
        self.busy = False
        self.last_sha = ''
        self.project_matches = True

    def git_event_objects(self):
        return SimpleNamespace(select_for_update=lambda: QS([self.event]),
            filter=lambda **kwargs: QS())

    def project_objects(self):
        return SimpleNamespace(filter=lambda **kwargs: QS([self.project] if self.project_matches else []))

    def environment_objects(self):
        return SimpleNamespace(filter=lambda **kwargs: QS([self.env]))

    def config_objects(self):
        return SimpleNamespace(filter=lambda **kwargs: QS([self.cfg]))

    def run_objects(self):
        def filter(**kwargs):
            if 'idempotency_key' in kwargs:
                return QS()
            if 'status__in' in kwargs:
                return QS([object()] if self.busy else [])
            if 'environment' in kwargs:
                return QS([SimpleNamespace(commit_sha=self.last_sha)] if self.last_sha else [])
            raise AssertionError(kwargs)
        return SimpleNamespace(filter=filter)


class AutoPushReliabilityTests(unittest.TestCase):
    def run_queue(self, db):
        state = SimpleNamespace(RECEIVED='RECEIVED', IGNORED='IGNORED', HANDLED='HANDLED', FAILED='FAILED')
        env = {
            'GitWebhookEvent': SimpleNamespace(objects=db.git_event_objects(), Status=state),
            'Project': SimpleNamespace(objects=db.project_objects(), Status=SimpleNamespace(ACTIVE='ACTIVE')),
            'Environment': SimpleNamespace(objects=db.environment_objects()),
            'DeploymentConfig': SimpleNamespace(objects=db.config_objects()),
            'DeploymentRun': SimpleNamespace(objects=db.run_objects(), Trigger=SimpleNamespace(GIT_PUSH='GIT_PUSH')),
            'SHA_RE': re.compile(r'[0-9a-fA-F]{40}'),
            'timezone': SimpleNamespace(now=lambda: datetime.now(timezone.utc)),
            'APIException': RuntimeError,
            'ValidationError': ValueError,
            'plan_deployment': lambda cfg_env: (db.cfg, {'cpu': 250}),
            'ensure_runner_ready': lambda: None,
            '_create_run': lambda *args, **kwargs: db.created.append((args, kwargs)),
        }
        exec(standalone_function(SERVICES, 'queue_verified_push'), env)
        return env['queue_verified_push'](db.event)

    def test_active_deployment_defers_event_then_retries(self):
        db = Database()
        db.busy = True
        self.assertEqual(self.run_queue(db), 0)
        self.assertEqual(db.event.status, 'RECEIVED')
        self.assertEqual(db.event.error_message, 'DEFERRED_ACTIVE_DEPLOYMENT')
        db.busy = False
        self.assertEqual(self.run_queue(db), 1)
        self.assertEqual(db.event.status, 'HANDLED')
        self.assertEqual(len(db.created), 1)
        self.assertEqual(db.created[0][1]['sha'], db.event.commit_sha)
        self.assertEqual(self.run_queue(db), 0)

    def test_disabled_auto_deploy_is_not_queued_and_reason_is_visible(self):
        db = Database()
        db.cfg.auto_deploy = False
        self.assertEqual(self.run_queue(db), 0)
        self.assertEqual(db.event.status, 'IGNORED')
        self.assertEqual(db.event.error_message, 'AUTO_DEPLOY_DISABLED')

    def test_already_deployed_sha_does_not_create_second_run(self):
        db = Database()
        db.last_sha = db.event.commit_sha
        self.assertEqual(self.run_queue(db), 0)
        self.assertEqual(db.event.status, 'HANDLED')
        self.assertFalse(db.created)

    def test_only_correct_repository_is_eligible(self):
        db = Database()
        db.project_matches = False
        self.assertEqual(self.run_queue(db), 0)
        self.assertEqual(db.event.status, 'IGNORED')
        self.assertFalse(db.created)

    def test_head_fallback_never_redeploys_same_commit(self):
        ns = {}
        exec(standalone_function(RECONCILE, 'needs_branch_deployment'), ns)
        self.assertFalse(ns['needs_branch_deployment']('ABCD', 'abcd'))
        self.assertFalse(ns['needs_branch_deployment']('', 'abcd'))
        self.assertTrue(ns['needs_branch_deployment']('1234', 'abcd'))

    def test_missing_webhook_head_reconciliation_creates_one_run(self):
        from contextlib import nullcontext
        sha = '9cbbc61' + 'a' * 33
        state = {'last': '4134784' + 'b' * 33, 'busy': False, 'created': []}
        config = SimpleNamespace(pk='cfg', auto_deploy=True,
                                 auto_deploy_branch='main', branch='main')

        class MockQuery(QS):
            def count(self): return len(self)
            def values_list(self, *args, **kwargs): return MockQuery([x.pk for x in self])
            def select_related(self, *args): return self
            def filter(self, **kwargs): return self
            def get(self, **kwargs): return self[0]

        class MockEnvironment:
            objects = None
            def __init__(self):
                self.pk = 'env'; self.project_id = 'project'
                self.project = SimpleNamespace(created_by='owner')

        obj = MockEnvironment()
        MockEnvironment.objects = SimpleNamespace(
            filter=lambda **kwargs: MockQuery([obj]),
            select_for_update=lambda: MockQuery([obj]))

        class MockRunObjects:
            def filter(self, **kwargs):
                if 'idempotency_key' in kwargs:
                    return MockQuery()
                if 'status__in' in kwargs:
                    return MockQuery([object()] if state['busy'] else [])
                return MockQuery([SimpleNamespace(commit_sha=state['last'])] if state['last'] else [])

        runs = MockRunObjects()
        environment = {
            'Environment': MockEnvironment,
            'Project': SimpleNamespace(Status=SimpleNamespace(ACTIVE='ACTIVE')),
            'DeploymentConfig': SimpleNamespace(objects=SimpleNamespace(filter=lambda **kw: MockQuery([config]))),
            'DeploymentRun': SimpleNamespace(objects=runs, Trigger=SimpleNamespace(GIT_PUSH='GIT_PUSH')),
            '_select_branch_commit': lambda owner, cfg: sha,
            'ensure_runner_ready': lambda: None,
            'plan_deployment': lambda env: (config, {'cpu': 250}),
            'transaction': SimpleNamespace(atomic=nullcontext),
            '_create_run': lambda *args, **kwargs: (state['created'].append(kwargs), state.__setitem__('last', kwargs['sha'])),
            'APIException': RuntimeError, 'ValidationError': ValueError,
            'logger': SimpleNamespace(warning=lambda *a, **kw: None, info=lambda *a, **kw: None),
            'needs_branch_deployment': lambda current, last: current.lower() != last.lower(),
        }
        exec(standalone_function(RECONCILE, 'reconcile_branch_heads'), environment)
        status = environment['reconcile_branch_heads'](limit=3)
        self.assertEqual(status['created'], 1)
        self.assertEqual(state['created'][0]['sha'], sha)
        status = environment['reconcile_branch_heads'](limit=3)
        self.assertEqual(status['created'], 0)
        state['last'] = 'other'
        state['busy'] = True
        self.assertEqual(environment['reconcile_branch_heads'](limit=3)['created'], 0)

    def test_worker_checks_pending_even_with_no_queued_run(self):
        text = WORKER.read_text(encoding='utf-8')
        self.assertLess(text.index('reconcile_received_pushes(limit=12)'),
                        text.index("if not DeploymentRun.objects.filter(status__in=['QUEUED', 'RUNNING']).exists():"))
        self.assertIn('if not busy and time.monotonic()', text)

    def test_webhook_hmac_precedes_enqueue_and_reports_real_counts(self):
        body = WEBHOOK.read_text(encoding='utf-8')
        self.assertLess(body.index('verify_signature(raw'), body.index('queue_verified_push(event)'))
        for fragment in ("'queued': queued", "'deferred': deferred", "'connections': matched"):
            self.assertIn(fragment, body)

    def test_admin_doctor_is_readonly_by_default(self):
        cli = APP_MANAGER.read_text(encoding='utf-8')
        self.assertIn("['github', 'push-doctor']", cli)
        self.assertIn("if options.apply:", cli)
        command = (ROOT / 'platform/backend/apps/deployments/runs/management/commands/autokom_push_doctor.py').read_text()
        self.assertIn('if options[\'apply\']:', command)
        self.assertNotIn('access_token_ciphertext', command)
        self.assertNotIn('client_secret', command)

if __name__ == '__main__':
    unittest.main()
