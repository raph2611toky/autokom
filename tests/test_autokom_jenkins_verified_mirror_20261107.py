"""Jenkins GCP install fallback: signed APT metadata, HTTPS mirrors, idempotence."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'ansible/roles/jenkins_server/files/autokom_jenkins_deb_fallback.py'
TASK_FILE = ROOT / 'ansible/roles/jenkins_server/tasks/main.yml'
spec = importlib.util.spec_from_file_location('autokom_jenkins_mirror', HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, redirected: str):
        super().__init__(data)
        self.redirected = redirected

    def geturl(self):
        return self.redirected


class JenkinsMirrorTests(unittest.TestCase):
    def setUp(self):
        self.data = b'x' * 1_000_000
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.policy = 'jenkins:\n Installed: (none)\n Candidate: 2.580.2\n Version table:\n'
        self.show = (f'Package: jenkins\nVersion: 2.580.2\nArchitecture: all\n'
                     f'Size: {len(self.data)}\nSHA256: {self.digest}\nFilename: binary/jenkins_2.580.2_all.deb\n\n')

    def test_signed_index_candidate_metadata(self):
        self.assertEqual(helper.candidate_metadata(self.policy, self.show),
                         ('2.580.2', self.digest, len(self.data)))

    def test_reject_candidate_not_in_index(self):
        with self.assertRaises(helper.DownloadError):
            helper.candidate_metadata(self.policy.replace('2.580.2', '2.999.9'), self.show)

    def test_reject_missing_sha256(self):
        with self.assertRaises(helper.DownloadError):
            helper.candidate_metadata(self.policy, self.show.replace('SHA256: ', 'NOT_SHA256: '))

    def test_reject_wrong_architecture(self):
        with self.assertRaises(helper.DownloadError):
            helper.candidate_metadata(self.policy, self.show.replace('Architecture: all', 'Architecture: amd64'))

    def test_mirror_fallback_verifies_hash(self):
        count = {'n': 0}
        def urlopen(request, timeout=45):
            count['n'] += 1
            if count['n'] <= 2:
                raise OSError('blocked first mirror')
            return FakeResponse(self.data, request.full_url)
        with tempfile.TemporaryDirectory() as d, patch.object(helper.urllib.request, 'urlopen', side_effect=urlopen):
            result = helper.download_verified('2.580.2', self.digest, len(self.data), Path(d))
            self.assertEqual(result.read_bytes(), self.data)
            self.assertEqual(count['n'], 3)
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)
            # An already verified local download should not contact the network.
            with patch.object(helper.urllib.request, 'urlopen', side_effect=AssertionError('unexpected network call')):
                self.assertEqual(helper.download_verified('2.580.2', self.digest, len(self.data), Path(d)), result)

    def test_reject_http_redirect_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d, patch.object(helper.urllib.request, 'urlopen', return_value=FakeResponse(self.data, 'http://mirror.invalid/pkg')):
            with self.assertRaises(helper.DownloadError):
                helper.download_verified('2.580.2', self.digest, len(self.data), Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_reject_bad_sha_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d, patch.object(helper.urllib.request, 'urlopen', side_effect=lambda request, timeout: FakeResponse(b'z'*len(self.data), request.full_url)):
            with self.assertRaises(helper.DownloadError):
                helper.download_verified('2.580.2', self.digest, len(self.data), Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_ansible_install_fallback_uses_apt_local_deb_not_dpkg_force(self):
        tasks = yaml.safe_load(TASK_FILE.read_text())
        install = next(t for t in tasks if t['name'] == 'Install Jenkins LTS with verified mirror fallback')
        self.assertEqual(install['block'][0]['ansible.builtin.apt']['name'], 'jenkins')
        self.assertIn('autokom_jenkins_deb_fallback.py', str(install['rescue'][0]))
        self.assertIn('autokom_jenkins_download.stdout', str(install['rescue'][1]))
        self.assertEqual(install['rescue'][1]['ansible.builtin.apt']['state'], 'present')

    def test_ansible_existing_autokom_override_is_idempotent_foreign_blocked(self):
        tasks = yaml.safe_load(TASK_FILE.read_text())
        check = next(t for t in tasks if t['name'].startswith('Refuse replacing a foreign Jenkins override'))
        write = next(t for t in tasks if t['name'].startswith('Bind new Jenkins installation'))
        expected = check['vars']['autokom_jenkins_expected_override']
        self.assertEqual(expected, write['ansible.builtin.copy']['content'])
        self.assertEqual(expected, '# Managed by AUTOKOM: phase-2\n[Service]\nEnvironment="JENKINS_LISTEN_ADDRESS=127.0.0.1"\n')
        self.assertIn('b64decode', str(check['ansible.builtin.assert']['that']))
        self.assertIn('islnk', str(check['ansible.builtin.assert']['that']))
        self.assertFalse(write['ansible.builtin.copy']['force'])


if __name__ == '__main__':
    unittest.main()
