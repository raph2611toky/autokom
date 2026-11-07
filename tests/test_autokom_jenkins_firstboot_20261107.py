"""Executable contracts for a first-admin-only Jenkins bootstrap (no live Jenkins)."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('jenkins_firstboot', ROOT / 'scripts/lib/jenkins_firstboot.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)
SPEC_POST = importlib.util.spec_from_file_location('jenkins_postinstall_new', ROOT / 'scripts/lib/jenkins_postinstall.py')
p = importlib.util.module_from_spec(SPEC_POST)
SPEC_POST.loader.exec_module(p)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'jenkins'
        self.home.mkdir()
        self.secrets = self.base / 'autokom-secrets'
        self.secrets.mkdir(mode=0o700)
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.patches = [
            mock.patch.object(b, 'JENKINS_HOME', self.home),
            mock.patch.object(b, 'SECRETS', self.secrets),
            mock.patch.object(b, 'PASSWORD', self.secrets / 'jenkins-first-admin.password'),
            mock.patch.object(b, 'PROFILE', self.secrets / 'jenkins-first-admin.profile.json'),
            mock.patch.object(b, 'OLD_PASSWORD', self.secrets / 'jenkins-first-admin.password.previous-auto'),
            mock.patch.object(b, 'ADMIN_MAIL_PROFILE', self.home / '.autokom-firstboot/admin.profile'),
            mock.patch.object(b, 'AUTH', self.secrets / 'jenkins-admin.auth'),
            mock.patch.object(b, 'STAGING', self.home / '.autokom-firstboot'),
            mock.patch.object(b, 'SEED', self.home / '.autokom-firstboot/admin.seed'),
            mock.patch.object(b, 'TOKEN', self.home / '.autokom-firstboot/admin.auth.pending'),
            mock.patch.object(b, 'SCRIPT', self.home / 'init.groovy.d/95-autokom-first-admin.groovy'),
            mock.patch.object(b, 'MATRIX_SCRIPT', self.home / 'init.groovy.d/96-autokom-first-admin-matrix.groovy'),
            mock.patch.object(b, 'MATRIX_PENDING', self.home / '.autokom-firstboot/matrix.pending'),
            mock.patch.object(b, 'MATRIX_DONE', self.home / '.autokom-firstboot/matrix.done'),
            mock.patch.object(b, 'METRICS_PASSWORD', self.secrets / 'jenkins-metrics.password'),
            mock.patch.object(b, 'METRICS_AUTH', self.secrets / 'jenkins-metrics.auth'),
            mock.patch.object(b, 'METRICS_SEED', self.home / '.autokom-firstboot/metrics.seed'),
            mock.patch.object(b, 'METRICS_TOKEN', self.home / '.autokom-firstboot/metrics.auth.pending'),
            mock.patch.object(b.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=self.uid, pw_gid=self.gid)),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.initial = self.home / 'secrets/initialAdminPassword'
        self.initial.parent.mkdir()
        self.initial.write_text('temporary-password')

    def test_secure_password_created_only_once(self):
        first = b._password()
        second = b._password()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(b.PASSWORD.stat().st_mode & 0o777, 0o600)
        self.assertEqual(b.PASSWORD.stat().st_uid, 0)

    def test_stage_only_managed_hook_and_private_seed(self):
        b._stage()
        self.assertTrue(b.SCRIPT.is_file())
        self.assertIn('AUTOKOM_FIRSTBOOT_READY', b.SCRIPT.read_text())
        self.assertEqual(b.SCRIPT.stat().st_mode & 0o777, 0o640)
        self.assertEqual(b.SEED.stat().st_mode & 0o777, 0o600)
        self.assertEqual(b.STAGING.stat().st_mode & 0o777, 0o700)
        self.assertTrue(b.SEED.read_text().startswith('autokom-admin\n'))
        b._stage()  # Idempotent without changing password.
        self.assertEqual(b.SEED.read_text().splitlines()[1], b.PASSWORD.read_text().strip())

    def test_foreign_startup_hook_must_never_be_overwritten(self):
        b.SCRIPT.parent.mkdir()
        b.SCRIPT.write_text('println("third-party")')
        with self.assertRaisesRegex(RuntimeError, 'non géré'):
            b._stage()
        self.assertEqual(b.SCRIPT.read_text(), 'println("third-party")')

    def test_foreign_secret_file_permissions_rejected(self):
        b.PASSWORD.write_text('x' * 42)
        b.PASSWORD.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'non protégé'):
            b._password()

    def test_no_unapproved_restart(self):
        with mock.patch.object(b.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'non autorisé'):
                b.bootstrap(approved=False)
            run.assert_not_called()

    def test_no_reconfiguration_of_existing_controller(self):
        (self.home / 'jenkins.install.UpgradeWizard.state').write_text('2.580')
        with mock.patch.object(b.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'déjà initialisé'):
                b.bootstrap(approved=True)
            run.assert_not_called()

    def test_bootstrap_carries_generated_token_to_private_root_directory(self):
        token_value = 'a' * 35
        b.save_admin_choice('admin-ikom', 'Administrateur IKOM', 'admin@ikom.mg', 'Secret!SuperLong@1234')
        def restarted(argv, **kwargs):
            self.assertEqual(argv, ['systemctl', 'restart', 'jenkins'])
            (self.home / 'jenkins.install.UpgradeWizard.state').write_text('2.580')
            b.TOKEN.write_text('admin-ikom:' + token_value + '\n')
            b.TOKEN.chmod(0o600)
            return SimpleNamespace(returncode=0)
        with mock.patch.object(b.subprocess, 'run', side_effect=restarted), \
             mock.patch.object(b, '_configured_secure', return_value=True), \
             mock.patch.object(b, '_probe_token', return_value=True):
            path = b.bootstrap(approved=True, timeout=5)
        self.assertEqual(path, str(b.AUTH))
        self.assertEqual(b.AUTH.read_text(), 'admin-ikom:' + token_value + '\n')
        self.assertEqual(b.AUTH.stat().st_mode & 0o777, 0o600)
        self.assertFalse(b.TOKEN.exists())
        self.assertFalse(b.SEED.exists())
        self.assertFalse(b.SCRIPT.exists())
        self.assertTrue(b.PASSWORD.exists())

    def test_root_auth_is_reused_without_restart(self):
        b.AUTH.write_text('autokom-admin:' + 'x' * 40 + '\n')
        b.AUTH.chmod(0o600)
        self.assertEqual(b.auth_file_if_available(), str(b.AUTH))
        with mock.patch.object(b.subprocess, 'run') as run:
            (self.home / 'jenkins.install.UpgradeWizard.state').write_text('2.580')
            self.assertEqual(b.bootstrap(approved=True), str(b.AUTH))
            run.assert_not_called()

    def test_invalid_token_does_not_replace_admin_auth(self):
        b._stage()
        b.TOKEN.write_text('attacker:bad\n')
        b.TOKEN.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'invalide'):
            b._recover_auth()
        self.assertFalse(b.AUTH.exists())

    def test_matrix_restriction_stage_and_finalization(self):
        b._stage()
        b.AUTH.write_text('autokom-admin:' + 'a' * 42 + '\n')
        b.AUTH.chmod(0o600)
        b.stage_matrix_restriction()
        self.assertTrue(b.matrix_restriction_pending())
        self.assertEqual(b.MATRIX_SCRIPT.stat().st_mode & 0o777, 0o640)
        self.assertEqual(b.MATRIX_PENDING.stat().st_mode & 0o777, 0o600)
        b.stage_matrix_restriction()
        with self.assertRaisesRegex(RuntimeError, 'non confirmée'):
            b.finalize_matrix_restriction()
        b.MATRIX_DONE.write_text('')
        b.MATRIX_DONE.chmod(0o600)
        (self.home/'config.xml').write_text('''<hudson><authorizationStrategy class="hudson.security.GlobalMatrixAuthorizationStrategy"><permission>hudson.model.Hudson.Administer:autokom-admin</permission></authorizationStrategy></hudson>''')
        b.finalize_matrix_restriction()
        self.assertFalse(b.MATRIX_SCRIPT.exists())
        self.assertFalse(b.matrix_restriction_pending())

    def test_prometheus_metrics_account_is_root_private_and_not_admin(self):
        b._stage()
        b.AUTH.write_text('autokom-admin:' + 'a' * 42 + '\n')
        b.AUTH.chmod(0o600)
        b.stage_matrix_restriction(include_metrics=True)
        self.assertEqual(b.METRICS_PASSWORD.stat().st_mode & 0o777, 0o600)
        self.assertEqual(b.METRICS_SEED.stat().st_mode & 0o777, 0o600)
        self.assertTrue(b.METRICS_SEED.read_text().startswith('autokom-metrics\n'))
        b.METRICS_TOKEN.write_text('autokom-metrics:' + 'b' * 44 + '\n')
        b.METRICS_TOKEN.chmod(0o600)
        b.MATRIX_DONE.write_text('')
        b.MATRIX_DONE.chmod(0o600)
        (self.home/'config.xml').write_text('''<hudson><authorizationStrategy class="hudson.security.GlobalMatrixAuthorizationStrategy"><permission>hudson.model.Hudson.Administer:autokom-admin</permission><permission>jenkins.metrics.api.Metrics.View:autokom-metrics</permission></authorizationStrategy></hudson>''')
        b.finalize_matrix_restriction()
        self.assertEqual(b.METRICS_AUTH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(b.METRICS_AUTH.read_text(), 'autokom-metrics:' + 'b'*44 + '\n')
        self.assertFalse(b.METRICS_TOKEN.exists())

    def test_restriction_cannot_overwrite_foreign_hook(self):
        b._stage()
        b.AUTH.write_text('autokom-admin:' + 'a' * 42 + '\n')
        b.AUTH.chmod(0o600)
        b.MATRIX_SCRIPT.write_text('println("other")')
        with self.assertRaisesRegex(RuntimeError, 'étranger'):
            b.stage_matrix_restriction()
        self.assertEqual(b.MATRIX_SCRIPT.read_text(), 'println("other")')

    def test_matrix_groovy_guards_existing_users(self):
        txt = b.MATRIX_TEMPLATE.read_text()
        for phrase in ('isSetupComplete()', 'User.getAll()',
                       'FullControlOnceLoggedInAuthorizationStrategy',
                       'matrix-auth', 'GlobalMatrixAuthorizationStrategy',
                       "strategy.add(Jenkins.ADMINISTER, adminUser)",
                       "strategy.add(jenkins.metrics.api.Metrics.VIEW, 'autokom-metrics')"):
            self.assertIn(phrase, txt)

    def test_groovy_requires_new_wizard_and_drops_temporary_admin(self):
        text = b.TEMPLATE.read_text()
        for phrase in ('isSetupComplete()', 'HudsonPrivateSecurityRealm',
                       "it.id != 'admin'", 'createAccount(', 'generateNewToken(',
                       'FullControlOnceLoggedInAuthorizationStrategy',
                       'INITIAL_SETUP_COMPLETED', "User.getById('admin', false)",
                       'setAllowAnonymousRead(false)'):
            self.assertIn(phrase, text)
        self.assertNotIn('AuthorizationStrategy.UNSECURED', text)


class MetricsWorkflowTests(unittest.TestCase):
    def test_autokom_metrics_read_only_auth_without_manual_prompt(self):
        import observability as o
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'jenkins-metrics.auth'
            path.write_text('autokom-metrics:' + 'b' * 44 + '\n')
            path.chmod(0o600)
            args = SimpleNamespace(mode='apply', non_interactive=False)
            with mock.patch.object(o, 'active', return_value=True), \
                 mock.patch.object(o, 'get_local', side_effect=[(403, ''), (200, '# HELP jenkins_job_count')]), \
                 mock.patch.object(o.Path, 'exists', return_value=True), \
                 mock.patch.object(b, 'METRICS_AUTH', path), \
                 mock.patch('jenkins_firstboot.METRICS_AUTH', path), \
                 mock.patch.object(o, 'prompt_text') as prompt, \
                 mock.patch.object(o, 'secret') as secret:
                self.assertEqual(o.ensure_jenkins_exporter(args), ('autokom-metrics', 'b' * 44))
                prompt.assert_not_called()
                secret.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def test_wizard_creates_first_admin_without_manual_web_wizard(self):
        args = p.parse(['--apply', '--interactive', '--profile', 'minimal', '--confirm-restart'])
        with mock.patch('os.geteuid', return_value=0), \
             mock.patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(p, 'jenkins_state', side_effect=['wizard', 'secure']), \
             mock.patch.object(p, 'trusted_web_url', return_value='https://jenkins.ikom.mg/'), \
             mock.patch.object(p, 'bootstrap_first_admin', return_value='/private/jenkins-admin.auth') as boot, \
             mock.patch.object(p, 'first_run_guidance') as manual, \
             mock.patch.object(p, 'auth_file_if_available', return_value=''), \
             mock.patch.object(p, 'choose_initial_administrator') as input_choice:

            p.configure(args)
            boot.assert_called_once()
            input_choice.assert_called_once()
            manual.assert_not_called()
            self.assertEqual(args.auth_file, '/private/jenkins-admin.auth')

    def test_existing_admin_is_never_replaced(self):
        args = p.parse(['--check', '--profile', 'minimal'])
        with mock.patch('os.geteuid', return_value=0), \
             mock.patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(p, 'jenkins_state', return_value='secure'), \
             mock.patch.object(p, 'trusted_web_url', return_value='https://jenkins.ikom.mg/'), \
             mock.patch.object(p, 'bootstrap_first_admin') as boot, \
             mock.patch.object(p, 'auth_file_if_available', return_value=''):
            p.configure(args)
            boot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
