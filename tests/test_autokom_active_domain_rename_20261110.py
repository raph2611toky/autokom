"""Offline regression for safe live-domain swap; never touch Cloudflare or Nginx."""
import ast
from contextlib import nullcontext
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/backend/apps/deployments/domains/services.py'


class Rejected(Exception):
    pass


class DomainState:
    next_id = 10

    def __init__(self, env, hostname, *, primary=False, active=False, org=None):
        DomainState.next_id += 1
        self.pk = self.id = DomainState.next_id
        self.environment = env
        self.environment_id = env.pk
        self.hostname = hostname
        self.organization = org
        self.domain_type = 'SUBDOMAIN'
        self.status = 'ACTIVE' if active else 'VERIFYING'
        self.verified_at = 'already-verified' if active else None
        self.tls_status = 'ACTIVE' if active else ''
        self.is_primary = primary
        self.is_disabled = False
        self.activation_error_code = ''
        self.verification_token_hash = ''
        self.deleted = False

    def save(self, update_fields):
        pass

    def delete(self):
        self.deleted = True


class Query:
    def __init__(self, records):
        self.records = records
        self.criteria = {}

    def select_for_update(self):
        return self

    def filter(self, **kwargs):
        found = [record for record in self.records if not record.deleted and all(
            getattr(record, key) == val for key, val in kwargs.items())]
        return Query(found)

    def exists(self):
        return bool(self.records)

    def first(self):
        return self.records[0] if self.records else None

    def get(self, **kwargs):
        item = self.filter(**kwargs).first()
        if item is None:
            raise RuntimeError('not found')
        return item

    def create(self, **kwargs):
        item = DomainState(kwargs['environment'], kwargs['hostname'], org=kwargs['organization'])
        item.status = kwargs['status']
        self.records.append(item)
        return item


class RenameTests(unittest.TestCase):
    def test_active_migration_activates_new_https_before_removing_old_and_changes_primary(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        records = [old]
        steps = []
        def broker(action, payload, timeout=1):
            steps.append(action)
            if action == 'domain.available':
                return {'available': True}
            return {}
        scope = self.run_rename_scope(env, project, records, broker)
        result = scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, 'newsite.auto.ikom.mg')
        self.assertEqual(result['domain'].hostname, 'newsite.auto.ikom.mg')
        self.assertTrue(result['domain'].is_primary)
        self.assertEqual(result['domain'].tls_status, 'ACTIVE')
        self.assertTrue(old.deleted)
        self.assertFalse(result['previous_retained'])
        self.assertLess(steps.index('domain.activate'), steps.index('domain.remove'))

    def test_failed_new_https_never_removes_original(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        records = [old]
        calls = []
        def broker(action, payload, timeout=1):
            calls.append((action,payload['hostname']))
            if action == 'domain.available':
                return {'available': True}
            if action == 'domain.activate':
                raise Rejected('new TLS unavailable')
            return {}
        scope = self.run_rename_scope(env, project, records, broker)
        with self.assertRaises(Rejected):
            scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, 'failed.auto.ikom.mg')
        self.assertFalse(old.deleted)
        self.assertTrue(old.is_primary)
        self.assertEqual(old.hostname, 'original.auto.ikom.mg')
        self.assertNotIn(('domain.remove', old.hostname), calls)

    def test_failed_old_cleanup_retains_old_as_non_primary_alias(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        def broker(action, payload, timeout=1):
            if action == 'domain.available':
                return {'available': True}
            if action == 'domain.remove':
                raise Rejected('old record needs reconciliation')
            return {}
        scope = self.run_rename_scope(env, project, [old], broker)
        result = scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, 'newsite.auto.ikom.mg')
        self.assertTrue(result['previous_retained'])
        self.assertFalse(old.deleted)
        self.assertFalse(old.is_primary)
        self.assertTrue(old.is_disabled)  # No accidental reactivation by reconciler
        self.assertTrue(result['domain'].is_primary)

    def test_identical_hostname_is_a_noop(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        calls = []
        scope = self.run_rename_scope(env, project, [old],
                                      lambda action, payload, timeout=1: calls.append(action))
        result = scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, old.hostname)
        self.assertTrue(result['activated'])
        self.assertFalse(result['previous_retained'])
        self.assertEqual(calls, [])

    def test_pending_domain_keeps_pending_status_and_does_not_issue_tls(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'old.auto.ikom.mg', org='org')
        old.status = 'PENDING'
        actions = []
        def broker(action, payload, timeout=1):
            actions.append(action)
            if action == 'domain.available':
                return {'available': True}
            return {}
        scope = self.run_rename_scope(env, project, [old], broker)
        result = scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, 'new.auto.ikom.mg')
        self.assertFalse(result['activated'])
        self.assertEqual(result['domain'].status, 'PENDING')
        self.assertEqual(actions, ['domain.available', 'domain.remove'])

    def test_reserved_prefix_is_rejected_without_writes(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        actions = []
        def broker(action, payload, timeout=1):
            actions.append(action)
            return {'available': False}
        scope = self.run_rename_scope(env, project, [old], broker)
        with self.assertRaises(Rejected):
            scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, 'taken.auto.ikom.mg')
        self.assertEqual(actions, ['domain.available'])
        self.assertTrue(old.is_primary)

    def test_reuse_existing_project_alias_after_dns_https_verification(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        existing = DomainState(env, 'known.auto.ikom.mg', org='org')
        existing.status = 'VERIFYING'
        existing.verification_token_hash = hashlib.sha256((str(existing.pk)*8).encode()).hexdigest()
        records = [old, existing]
        steps = []
        def broker(action, payload, timeout=1):
            steps.append((action, payload['hostname']))
            if action == 'domain.reuse.check':
                return {'available': True, 'reason': 'owned_by_project'}
            return {}
        scope = self.run_rename_scope(env, project, records, broker)
        result = scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, existing.hostname)
        self.assertIs(result['domain'], existing)
        self.assertTrue(existing.is_primary)
        self.assertTrue(old.deleted)
        self.assertEqual(len(records), 2)  # Reused an ID, no duplicate reservation.
        self.assertLess(steps.index(('domain.activate', existing.hostname)),
                        steps.index(('domain.remove', old.hostname)))
        self.assertNotIn(('domain.remove', existing.hostname), steps)

    def test_reuse_other_environment_alias_is_always_forbidden(self):
        env = SimpleNamespace(pk='environment-id')
        foreign = SimpleNamespace(pk='another-environment')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        existing = DomainState(foreign, 'known.auto.ikom.mg', org='org')
        actions = []
        scope = self.run_rename_scope(env, project, [old, existing],
                                      lambda action, payload, timeout=1: actions.append(action))
        with self.assertRaises(Rejected):
            scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, existing.hostname)
        self.assertEqual(actions, [])
        self.assertTrue(old.is_primary)

    def test_failed_reuse_does_not_delete_existing_alias_or_original(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        existing = DomainState(env, 'known.auto.ikom.mg', org='org')
        existing.verification_token_hash = hashlib.sha256((str(existing.pk)*8).encode()).hexdigest()
        operations = []
        def broker(action, payload, timeout=1):
            operations.append((action, payload['hostname']))
            if action == 'domain.reuse.check':
                return {'available': True, 'reason': 'owned_by_project'}
            if action == 'domain.activate':
                raise Rejected('certificate failed')
            return {}
        scope = self.run_rename_scope(env, project, [old, existing], broker)
        with self.assertRaises(Rejected):
            scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, existing.hostname)
        self.assertTrue(old.is_primary)
        self.assertFalse(old.deleted)
        self.assertFalse(existing.deleted)
        self.assertNotIn(('domain.remove', existing.hostname), operations)
        self.assertNotIn(('domain.remove', old.hostname), operations)

    def test_same_environment_existing_dns_mismatch_is_not_reusable(self):
        env = SimpleNamespace(pk='environment-id')
        project = SimpleNamespace(pk='project-id', organization='org')
        old = DomainState(env, 'original.auto.ikom.mg', primary=True, active=True, org='org')
        existing = DomainState(env, 'known.auto.ikom.mg', org='org')
        existing.verification_token_hash = hashlib.sha256((str(existing.pk)*8).encode()).hexdigest()
        actions = []
        def broker(action, payload, timeout=1):
            actions.append(action)
            return {'available': False, 'reason': 'dns_target_mismatch'}
        scope = self.run_rename_scope(env, project, [old, existing], broker)
        with self.assertRaises(Rejected):
            scope['rename_pending_managed_domain'](object(), 1, 1, old.pk, existing.hostname)
        self.assertEqual(actions, ['domain.reuse.check'])
        self.assertTrue(old.is_primary)

    @staticmethod
    def run_rename_scope(env, project, records, broker):
        source = ast.parse(SOURCE.read_text())
        code = compile(ast.Module(body=[next(node for node in source.body if isinstance(node, ast.FunctionDef) and
                                          node.name == 'rename_pending_managed_domain')], type_ignores=[]), str(SOURCE), 'exec')
        query = Query(records)
        class Objects:
            @staticmethod
            def select_for_update():
                return SimpleNamespace(get=lambda **kw: project)
        scope = {
            'get_environment': lambda *args, **kwargs: (project, env),
            'transaction': SimpleNamespace(atomic=nullcontext),
            '_reject_domain_mutation_during_deployment': lambda *args: None,
            '_validate_managed_rename_host': lambda h: h,
            'Domain': SimpleNamespace(objects=query, Type=SimpleNamespace(SUBDOMAIN='SUBDOMAIN'),
                 Status=SimpleNamespace(PENDING='PENDING', ACTIVE='ACTIVE', VERIFYING='VERIFYING',ERROR='ERROR')),
            'ValidationError': Rejected,
            'request_control': broker,
            'hashlib': hashlib,
            '_challenge': lambda domain: str(domain.pk) * 8,
            'hmac': __import__('hmac'),
            'PermissionDenied': Rejected,
            'timezone': SimpleNamespace(now=lambda: 'today'),
        }
        # imports Project inside function: simulate via mock sys.modules if a Django
        # environment is unavailable, using the real production model path.
        import sys
        module_path = 'apps.projects.project.models'
        old = sys.modules.get(module_path)
        from types import ModuleType
        mock_module = ModuleType(module_path)
        mock_module.Project = SimpleNamespace(objects=Objects())
        sys.modules[module_path] = mock_module
        sock_path = 'apps.deployments.runs.autokom_socket'
        old_sock = sys.modules.get(sock_path)
        mock_sock = ModuleType(sock_path)
        mock_sock.AutokomControlUnavailable = type('AutokomControlUnavailable', (Exception,), {})
        sys.modules[sock_path] = mock_sock
        # The test is synchronous. Restore module by addCleanup in caller? Keep
        # this isolated fake only while executing compiled function.
        def execute(*args):
            try:
                return scope['actual_rename'](*args)
            finally:
                if old is None:
                    sys.modules.pop(module_path, None)
                else:
                    sys.modules[module_path] = old
                if old_sock is None:
                    sys.modules.pop(sock_path, None)
                else:
                    sys.modules[sock_path] = old_sock
        helper_defs = [node for node in source.body if isinstance(node, ast.FunctionDef) and
                       node.name in ('_same_environment_managed_target', '_managed_destination_check')]
        exec(compile(ast.Module(body=helper_defs, type_ignores=[]), str(SOURCE), 'exec'), scope)
        exec(code, scope)
        scope['actual_rename'] = scope['rename_pending_managed_domain']
        scope['rename_pending_managed_domain'] = execute
        return scope


if __name__ == '__main__':
    unittest.main()
