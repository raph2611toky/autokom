"""Jenkins First-Run + auth token onboarding: all external services are mocked."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('jenkins_onboarding', ROOT/'scripts/lib/jenkins_postinstall.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class JenkinsOnboardingTests(unittest.TestCase):
    def test_state_secure_with_official_marker(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'jenkins.install.UpgradeWizard.state').write_text('2.500')
            with mock.patch.object(m,'JENKINS_HOME',home), mock.patch.object(m,'jenkins_xml_secure',return_value=True):
                self.assertEqual(m.jenkins_state(),'secure')
    def test_state_wizard_initial_admin_and_secure_xml(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'secrets').mkdir(); (home/'secrets/initialAdminPassword').write_text('private')
            (home/'users/admin_12345').mkdir(parents=True)
            with mock.patch.object(m,'JENKINS_HOME',home), mock.patch.object(m,'jenkins_xml_secure',return_value=True):
                self.assertEqual(m.jenkins_state(),'wizard')
    def test_state_existing_insecure_no_reset(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'jenkins.install.UpgradeWizard.state').write_text('2.500')
            with mock.patch.object(m,'JENKINS_HOME',home), mock.patch.object(m,'jenkins_xml_secure',return_value=False):
                self.assertEqual(m.jenkins_state(),'unsafe')
    def test_state_wizard_finished_marker_transition(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'secrets').mkdir(); (home/'secrets/initialAdminPassword').write_text('private')
            with mock.patch.object(m,'JENKINS_HOME',home), mock.patch.object(m,'jenkins_xml_secure',return_value=True):
                self.assertEqual(m.jenkins_state(),'wizard')
                (home/'jenkins.install.UpgradeWizard.state').write_text('2.500')
                self.assertEqual(m.jenkins_state(),'secure')
    def test_url_https_explicit_restrictions(self):
        self.assertEqual(m.trusted_web_url('https://jenkins.ik0m.tech/'), 'https://jenkins.ik0m.tech/')
        for url in ('http://jenkins.ik0m.tech','https://a.test/path','https://admin:pass@a.test','https://a.test:8080'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                m.trusted_web_url(url)
    def test_temp_auth_removed_and_private(self):
        with m.transient_credentials('admin', 'TOKEN_SENSITIVE') as path:
            self.assertTrue(Path(path).is_file())
            self.assertEqual(Path(path).stat().st_mode & 0o777,0o600)
            self.assertEqual(Path(path).read_text(), 'admin:TOKEN_SENSITIVE\n')
            self.assertEqual(Path(path).parent.stat().st_mode & 0o777,0o700)
        self.assertFalse(Path(path).exists())
        self.assertFalse(Path(path).parent.exists())
    def test_temp_auth_removed_on_failure(self):
        with self.assertRaisesRegex(RuntimeError,'fake failure'):
            with m.transient_credentials('admin', 'TOKEN_SENSITIVE') as path:
                raise RuntimeError('fake failure')
        self.assertFalse(Path(path).exists())
    def test_masked_input_username_and_token_no_manual_file(self):
        args=m.parse(['--apply','--interactive'])
        with mock.patch.object(m,'ask',return_value='my-admin'), mock.patch.object(m,'read_masked',return_value='API_TOKEN'), \
             mock.patch.object(m,'ensure_cli_jar'), mock.patch.object(m,'verify_admin'):
            with m.authorization_context(args,'https://jenkins.ik0m.tech/',apply=True) as path:
                self.assertEqual(Path(path).read_text(), 'my-admin:API_TOKEN\n')
            self.assertFalse(Path(path).exists())
    def test_script_does_not_request_token_in_check(self):
        args=m.parse(['--check','--interactive'])
        with mock.patch.object(m,'ask') as ask:
            with m.authorization_context(args,'https://jenkins.ik0m.tech/',apply=False) as result:
                self.assertEqual(result, '')
            ask.assert_not_called()
    def test_interactive_optional_plugin_select_yes(self):
        args=m.parse(['--check','--interactive'])
        with mock.patch.object(m, 'pick', side_effect=[1,2]) as pick:
            m.interactive_configure(args)
        self.assertEqual(pick.call_count,2)
        self.assertTrue(args.multibranch_scan)
        self.assertIn('multibranch-scan-webhook-trigger',m.plugin_ids(args.profile,args.multibranch_scan))

    def test_interactive_optional_plugin_select_no(self):
        args=m.parse(['--check','--interactive'])
        with mock.patch.object(m, 'pick', side_effect=[1,1]):
            m.interactive_configure(args)
        self.assertFalse(args.multibranch_scan)
        self.assertNotIn('multibranch-scan-webhook-trigger',m.plugin_ids(args.profile,args.multibranch_scan))

    def test_explicit_flag_skips_extra_menu(self):
        args=m.parse(['--check','--interactive','--multibranch-scan'])
        with mock.patch.object(m,'pick',return_value=1) as pick:
            m.interactive_configure(args)
        self.assertTrue(args.multibranch_scan)
        self.assertEqual(pick.call_count,1)

    def test_optional_manifest_and_no_duplicate_plugins(self):
        plugins=m.plugin_ids('devsecops',True)
        self.assertEqual(plugins.count('multibranch-scan-webhook-trigger'),1)
        self.assertNotIn('multibranch-scan-webhook-trigger',m.plugin_ids('devsecops'))

    def test_quick_mode_only_one_menu(self):
        args=m.parse(['--apply','--interactive'])
        with mock.patch.object(m,'pick',side_effect=[1,1]) as pick:
            m.interactive_configure(args)
        self.assertEqual(pick.call_count,2)
        self.assertEqual(args.profile,'devsecops')
        self.assertTrue(args.harden)
        self.assertTrue(args.confirm_restart)
    def test_auth_promise_guard_before_plugin_modification(self):
        args=m.parse(['--apply','--interactive','--profile','devsecops'])
        args.confirm_restart=True
        with mock.patch('os.geteuid',return_value=0), mock.patch('subprocess.run') as svc, \
             mock.patch.object(m,'jenkins_state',return_value='secure'), \
             mock.patch.object(m,'trusted_web_url',return_value='https://jenkins.ik0m.tech/'), \
             mock.patch.object(m,'local_plugin_ids',return_value=set()), \
             mock.patch.object(m,'authorization_context') as auth, \
             mock.patch.object(m,'ensure_cli_jar') as jar, mock.patch.object(m,'cli') as cli:
            svc.return_value.returncode=0
            auth.side_effect=RuntimeError('No token')
            with self.assertRaisesRegex(RuntimeError,'No token'):
                m.configure(args)
            jar.assert_not_called()
            cli.assert_not_called()
    def test_first_run_check_no_wait(self):
        with mock.patch.object(m,'jenkins_state',return_value='wizard'), mock.patch('time.sleep') as sleep:
            self.assertFalse(m.first_run_guidance('https://example.com/',apply=False))
            sleep.assert_not_called()

if __name__=='__main__':
    unittest.main()
