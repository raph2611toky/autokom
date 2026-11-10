"""Regression tests for DevOps npm lock reconciliation and safe DNS removal.

No network, npm registry, Cloudflare, Docker, Nginx or Certbot calls.
"""
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.runtime.autokom_npm_install import install_step
import autokom_platform_broker as broker

ENV = '11111111-1111-4111-8111-111111111111'
HOST = 'portfolio.auto.ikom.mg'


class LockfilePolicyTests(unittest.TestCase):
    def test_devops_retries_only_a_lockfile_mismatch(self):
        step = install_step('DEVOPS')
        self.assertIn('RUN set -eu;', step)
        self.assertIn('can only install packages when your package.json', step)
        self.assertIn('npm install --package-lock-only --ignore-scripts', step)
        self.assertIn('AUTOKOM_NPM_LOCK_REPAIRED:', step)
        self.assertIn('npm ci --ignore-scripts', step)

    def test_shell_fallback_only_on_npm_lock_mismatch(self):
        # Real /bin/sh execution against a fake npm, not the public registry.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / 'npm'
            binary.write_text('#!/bin/sh\n'
                              'if [ "$1" = "ci" ] && [ ! -f "' + str(root / 'repaired') + '" ]; then\n'
                              "  echo 'npm error `npm ci` can only install packages when your package.json and package-lock.json or npm-shrinkwrap.json are in sync.' >&2\n"
                              '  exit 1\n'
                              'fi\n'
                              'if [ "$1" = "install" ]; then touch "' + str(root / 'repaired') + '"; fi\n'
                              'exit 0\n')
            binary.chmod(0o755)
            command = install_step('DEVOPS').removeprefix('RUN ').strip()
            run = subprocess.run(['sh', '-c', command], capture_output=True, text=True,
                                 env={'PATH': str(root) + os.pathsep + os.environ['PATH']}, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((root / 'repaired').exists())
            self.assertIn('AUTOKOM_NPM_LOCK_REPAIRED', run.stdout)

    def test_devsecops_stays_strict_and_reproducible(self):
        for mode in ('DEVSECOPS', None, 'INVALID'):
            with self.subTest(mode=mode):
                step = install_step(mode)
                self.assertEqual(step, 'RUN npm ci --ignore-scripts --no-audit --no-fund\n')
                self.assertNotIn('npm install --package-lock-only', step)


class DomainDeletionTests(unittest.TestCase):
    def test_cloudflare_delete_requires_record_provenance(self):
        with patch.object(broker, '_cloudflare_settings', return_value={
            'suffix': 'auto.ikom.mg', 'zone': 'f'*32, 'token': 'test-token',
            'address': '136.116.89.163',
        }):
            record = {'id': 'a'*32, 'name': HOST, 'type': 'A',
                      'content': '136.116.89.163', 'proxied': False}
            class Response(io.BytesIO):
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
            def get_only(request, timeout=12):
                if request.get_method() != 'GET':
                    self.fail('Refused to delete an unowned Cloudflare record')
                return Response(json.dumps({'success': True, 'result': [record]}).encode())
            with patch.object(broker.urllib.request, 'urlopen', side_effect=get_only):
                with self.assertRaisesRegex(broker.BrokerError, 'AUTOKOM_DOMAIN_DNS_UNOWNED'):
                    broker._cloudflare_remove_owned_record(HOST)
            record['comment'] = 'AUTOKOM managed A record'
            methods = []
            def get_delete(request, timeout=12):
                methods.append(request.get_method())
                response = {'success': True, 'result': [record]} if request.get_method() == 'GET' else {'success': True}
                return Response(json.dumps(response).encode())
            with patch.object(broker.urllib.request, 'urlopen', side_effect=get_delete):
                self.assertTrue(broker._cloudflare_remove_owned_record(HOST)['deleted'])
            self.assertEqual(methods, ['GET', 'DELETE'])

    def test_custom_domain_deletion_never_calls_cloudflare(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(broker, 'STATE', Path(folder)), \
             patch.object(broker, '_runtime_dns_env', return_value={'AUTOKOM_MANAGED_DOMAIN_SUFFIX': 'auto.ikom.mg'}), \
             patch.object(broker, '_deactivate_owned_vhost', return_value=False) as deactivate, \
             patch.object(broker, '_cloudflare_remove_owned_record') as cf:
            result = broker._remove_owned_domain({'hostname': 'example.org', 'environment_id': ENV, 'managed': False})
            self.assertTrue(result['removed'])
            deactivate.assert_called_once_with('example.org', ENV)
            cf.assert_not_called()


if __name__ == '__main__': unittest.main()
