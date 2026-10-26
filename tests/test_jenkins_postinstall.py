import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('module', ROOT/'scripts/lib/jenkins_postinstall.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PostInstallTests(unittest.TestCase):
    def test_profiles(self):
        minimal=m.plugin_ids('minimal')
        base=m.plugin_ids('baseline')
        dev=m.plugin_ids('devsecops')
        self.assertFalse(minimal)
        self.assertIn('pipeline-stage-view', base)
        self.assertIn('github-branch-source', base)
        self.assertIn('generic-webhook-trigger', dev)
        self.assertIn('prometheus', dev)
        self.assertNotIn('multibranch-scan-webhook-trigger', dev)
        self.assertEqual(len(dev), len(set(dev)))

    def test_risky_explicit(self):
        self.assertIn('multibranch-scan-webhook-trigger', m.plugin_ids('devsecops', True))

    def test_auth_must_be_root_private(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'auth'; p.write_text('user:token'); p.chmod(0o600)
            with mock.patch('os.geteuid',return_value=0):
                self.assertEqual(m.secure_file(p),p)
            p.chmod(0o644)
            with self.assertRaises(ValueError):m.secure_file(p)

    def test_xml_rejects_unsecured(self):
        with tempfile.TemporaryDirectory() as d:
            original=m.JENKINS_HOME
            m.JENKINS_HOME=Path(d)
            try:
                (Path(d)/'config.xml').write_text('<hudson><useSecurity>false</useSecurity></hudson>')
                self.assertFalse(m.jenkins_xml_secure())
                (Path(d)/'config.xml').write_text('<hudson><useSecurity>true</useSecurity><securityRealm class="hudson.security.HudsonPrivateSecurityRealm"/><authorizationStrategy class="hudson.security.GlobalMatrixAuthorizationStrategy"/></hudson>')
                self.assertTrue(m.jenkins_xml_secure())
            finally:m.JENKINS_HOME=original

    def test_admin_secret_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'secret'; f.write_text('a'*16+'\n'); f.chmod(0o600)
            with mock.patch.object(m,'cli') as cli, mock.patch.object(m,'SENSITIVE',Path(d)/'secrets'):
                with self.assertRaises(ValueError):m.handle_admin('/root/token','ab','%s'%f,True)
                cli.assert_not_called()

    def test_restart_approval_before_modification(self):
        args=m.parse(['--apply','--profile','devsecops','--auth-file','/root/token'])
        with mock.patch('os.geteuid', return_value=0), \
             mock.patch('subprocess.run') as r, \
             mock.patch.object(m,'jenkins_state',return_value='secure'), \
             mock.patch.object(m,'trusted_web_url',return_value='https://jenkins.example.com/'), \
             mock.patch.object(m,'local_plugin_ids',return_value=set()), \
             mock.patch.object(m,'secure_file',return_value=Path('/root/token')), \
             mock.patch.object(m,'ensure_cli_jar') as jar:
            r.return_value.returncode=0
            with self.assertRaisesRegex(RuntimeError,'--confirm-restart'):
                m.configure(args)
            jar.assert_not_called()

if __name__=='__main__':unittest.main()
