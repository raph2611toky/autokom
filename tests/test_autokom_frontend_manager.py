"""Tests sans root, sans réseau et sans modifier la production."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'scripts/lib/autokom_frontend_manager.py'
spec = importlib.util.spec_from_file_location('autokom_frontend_test_module', PATH)
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)


class FrontendManagerTests(unittest.TestCase):
    def test_domain_refusals(self):
        self.assertEqual(fm.domain('Auto.IK0M.tech'), 'auto.ik0m.tech')
        for invalid in ('localhost', 'invalid.local', 'a.test', 'evil;rm -rf /', 'http://host.test'):
            with self.subTest(invalid=invalid), self.assertRaises(fm.FrontendError):
                fm.domain(invalid)

    def test_nginx_reverse_proxy_preserves_api_and_spa(self):
        text = fm.nginx_config('auto.ik0m.tech', 'backend.auto.ik0m.tech', 8172)
        self.assertIn('try_files $uri $uri/ /index.html;', text)
        self.assertIn('location ^~ /api/', text)
        self.assertIn('proxy_pass http://127.0.0.1:8172;', text)
        self.assertIn('proxy_set_header Host backend.auto.ik0m.tech;', text)
        self.assertNotIn('proxy_pass http://56.', text)
        self.assertIn('/var/www/autokom/frontend/current', text)

    def test_setup_check_no_writes(self):
        args = __import__('argparse').Namespace(domain='auto.ik0m.tech', backend_domain='backend.auto.ik0m.tech',
            tls_mode='existing', email=None, nginx_mode='existing', apply=False, check=True)
        with mock.patch.object(fm, 'ensure_source'), mock.patch.object(fm, 'config_urls',
             return_value=('auto.ik0m.tech','backend.auto.ik0m.tech')), \
             mock.patch.object(fm, 'save_state') as save, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fm.setup(args), 0)
        save.assert_not_called()

    def test_frontend_cli_commands_have_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as exit_:
            fm.main(['deploy', '-h'])
        self.assertEqual(exit_.exception.code, 0)
        self.assertIn('--check', out.getvalue())
        self.assertIn('--apply', out.getvalue())

    def test_release_id_restricted(self):
        for value in ('../../root', '20261104T142200Z-12345678/../', 'abcdef'):
            self.assertIsNone(fm.RELEASE_ID.fullmatch(value))

    def test_source_and_lock_consistent(self):
        fm.ensure_source()


if __name__ == '__main__':
    unittest.main()
