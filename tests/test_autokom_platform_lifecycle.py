"""Pure standard-library tests. Never require root/network/MySQL."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, HERE/path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APP = module('autokom_app_manager', Path('scripts/lib/autokom_app_manager.py'))
GITHUB = module('autokom_private_update', Path('scripts/lib/autokom_private_update.py'))


class LifecycleTests(unittest.TestCase):
    def test_domain_validation(self):
        self.assertEqual(APP.validate_domain('API.Example.org.'), 'api.example.org')
        for domain in ('', 'localhost', '*.evil.org', 'evil.org;rm -rf /',
                       'api.local', 'a..b.org', 'evil.org\nserver_name x.org'):
            with self.subTest(domain=domain), self.assertRaises(APP.AppError):
                APP.validate_domain(domain)

    def test_private_repo_requires_https_allowlist_and_clean_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '-q', '-b', 'toky'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                            'commit', '-qm', 'init', '--allow-empty'], cwd=repo, check=True)
            subprocess.run(['git', 'remote', 'add', 'origin',
                            'https://github.com/raph2611toky/autokom.git'], cwd=repo, check=True)
            self.assertEqual(GITHUB._repo(repo), ('raph2611toky', 'autokom', 'toky'))
            self.assertEqual(GITHUB.update(repo, check=True), 0)
            (repo/'dirty.txt').write_text('untracked')
            with self.assertRaises(GITHUB.UpdateError):
                GITHUB._repo(repo)

    def test_no_stored_auth_token_in_repo(self):
        source = (HERE/'scripts/lib/autokom_private_update.py').read_text()
        self.assertIn('GIT_ASKPASS', source)
        self.assertIn('TemporaryDirectory', source)
        self.assertNotIn('credential.helper store', source)

    def test_django_health_is_single_api_view_get(self):
        source = (HERE/'platform/backend/config/health.py').read_text()
        self.assertIn('class PlatformHealthAPIView(APIView)', source)
        self.assertIn('def get(', source)
        self.assertNotIn('def post(', source)


if __name__ == '__main__':
    unittest.main()
