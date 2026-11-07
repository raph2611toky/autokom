"""Offline tests for automatic recovery (no AWS, Cloudflare, Certbot or Jenkins)."""
import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import autokom_platform_broker as broker

ROOT = Path(__file__).resolve().parents[1]
ENV = '674be29f-4fe2-4d68-b1c9-554e561b8923'
RUN = '0bba85bc-c668-480c-8a41-d1b037403ee7'


def load_matcher():
    source = (ROOT/'platform/backend/apps/deployments/domains/reconciler.py').read_text()
    tree = ast.parse(source)
    fun = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == 'matches_active_release')
    namespace = {}
    exec(compile(ast.Module(body=[fun], type_ignores=[]), '<matcher>', 'exec'), namespace)
    return namespace['matches_active_release']


class SelfHealingTests(unittest.TestCase):
    def test_reconciler_accepts_previous_healthy_release_after_failed_jenkins_run(self):
        # A later failed deployment must not block repair of the still-active one.
        matches = load_matcher()
        release = SimpleNamespace(source_deployment_id=RUN)
        self.assertTrue(matches({'environment_id':ENV,'run_id':RUN}, ENV, release))
        self.assertFalse(matches({'environment_id':ENV,'run_id':'another-run'}, ENV, release))
        self.assertFalse(matches({'environment_id':'another-env','run_id':RUN}, ENV, release))
        self.assertFalse(matches(None, ENV, release))

    def test_automatic_timer_and_config_event_watcher_installed_and_enabled(self):
        role=(ROOT/'ansible/roles/platform_runtime/tasks/main.yml').read_text()
        timer=(ROOT/'platform/backend/deployment_templates/autokom-domain-reconciler.timer.example').read_text()
        path=(ROOT/'platform/backend/deployment_templates/autokom-domain-reconciler.path.example').read_text()
        self.assertIn('autokom-domain-reconciler.timer', role)
        self.assertIn('autokom-domain-reconciler.path', role)
        self.assertIn('OnActiveSec=45s', timer)
        self.assertIn('OnUnitInactiveSec=2min', timer)
        for name in ('runtime.env','cloudflare-dns.env','backend.env'):
            self.assertIn('PathChanged=/etc/autokom/secrets/'+name,path)
        self.assertIn('Unit=autokom-domain-reconciler.service',path)
        self.assertIn('when: not ansible_check_mode',role)

    def test_nginx_certbot_mutation_is_serialized_with_deployment(self):
        with patch.object(broker,'_runtime_lock_call',return_value={'active':True}) as lock:
            self.assertEqual(broker.handle('domain.activate',{'hostname':'portfolio.auto.ikom.mg',
                              'environment_id':ENV,'managed':True}),{'active':True})
            self.assertIs(lock.call_args.args[0],broker._activate_owned_domain)
        source=(ROOT/'scripts/lib/autokom_platform_broker.py').read_text()
        self.assertIn("_check_dns(host,params.get('token'),params.get('managed') is True)",source)

    def test_recovery_never_dispatches_jenkins_and_retains_failed_attempt_backoff(self):
        source=(ROOT/'platform/backend/apps/deployments/domains/reconciler.py').read_text()
        self.assertIn('RETRY = timedelta(minutes=5)',source)
        self.assertIn("request_control('runtime.status'",source)
        self.assertIn('matches_active_release(',source)
        self.assertNotIn('jenkins.dispatch',source)
        self.assertIn('activation_error_code=code',source)

    def test_runtime_doctor_checks_auto_service_status(self):
        source=(ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        self.assertIn("('autokom-domain-reconciler.timer', 'autokom-domain-reconciler.path')",source)
        self.assertIn('failures.append(unit)',source)

if __name__=='__main__':unittest.main()
