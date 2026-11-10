"""Offline regression for deleting domains: keep one, protect primary & ownership.

Uses the production function body with isolated fakes: no real Django DB,
no Cloudflare, Nginx, certificate or broker side effects.
"""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'platform/backend/apps/deployments/domains/services.py'


class DomainValidationError(Exception):
    pass


class FakeQuerySet:
    def __init__(self, items):
        self.items = list(items)

    def select_for_update(self):
        return self

    def filter(self, **criteria):
        return FakeQuerySet([item for item in self.items
                             if all(getattr(item, key) == value for key, value in criteria.items())])

    def first(self):
        return self.items[0] if self.items else None

    def count(self):
        return len(self.items)


class FakeDomain:
    def __init__(self, number, environment, primary=False):
        self.pk = number
        self.environment = environment
        self.hostname = f'site-{number}.auto.ikom.mg'
        self.domain_type = 'SUBDOMAIN'
        self.is_primary = primary
        self.deleted = False

    def delete(self):
        self.deleted = True


def compile_remove_domain():
    source = ast.parse(SERVICE.read_text())
    method = next(n for n in source.body
                  if isinstance(n, ast.FunctionDef) and n.name == 'remove_domain')
    return compile(ast.Module(body=[method], type_ignores=[]), str(SERVICE), 'exec')


class DomainMinimumOneTests(unittest.TestCase):
    def run_deletion(self, count, target=1, primary=None, broker_error=False):
        environment = SimpleNamespace(pk=42)
        domains = [FakeDomain(i, environment, primary=(i == primary))
                   for i in range(1, count + 1)]
        calls = []
        def broker(name, payload, timeout):
            calls.append((name, payload))
            if broker_error:
                raise RuntimeError('broker unavailable')
        scope = {
            'get_environment': lambda *args, **kwargs: (None, environment),
            'transaction': SimpleNamespace(atomic=nullcontext),
            'Domain': SimpleNamespace(objects=FakeQuerySet(domains),
                                      Type=SimpleNamespace(SUBDOMAIN='SUBDOMAIN')),
            '_reject_domain_mutation_during_deployment': lambda env: None,
            'ValidationError': DomainValidationError,
            'request_control': broker,
        }
        exec(compile_remove_domain(), scope)
        try:
            scope['remove_domain'](object(), 1, 2, target)
            error = None
        except Exception as exc:
            error = exc
        return domains, calls, error

    def test_last_remaining_domain_cannot_be_deleted(self):
        domains, calls, error = self.run_deletion(1)
        self.assertIsInstance(error, DomainValidationError)
        self.assertFalse(domains[0].deleted)
        self.assertEqual(calls, [])

    def test_two_domains_allow_one_non_primary_deletion(self):
        domains, calls, error = self.run_deletion(2, target=2, primary=1)
        self.assertIsNone(error)
        self.assertFalse(domains[0].deleted)
        self.assertTrue(domains[1].deleted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'domain.remove')

    def test_primary_remains_protected_with_two_domains(self):
        domains, calls, error = self.run_deletion(2, target=1, primary=1)
        self.assertIsInstance(error, DomainValidationError)
        self.assertTrue(all(not domain.deleted for domain in domains))
        self.assertEqual(calls, [])

    def test_three_domains_still_allow_deletion(self):
        domains, calls, error = self.run_deletion(3, target=2, primary=1)
        self.assertIsNone(error)
        self.assertTrue(domains[1].deleted)
        self.assertEqual(len(calls), 1)

    def test_failed_broker_never_deletes_database_reservation(self):
        domains, calls, error = self.run_deletion(2, target=2, primary=1, broker_error=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertTrue(all(not d.deleted for d in domains))
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
