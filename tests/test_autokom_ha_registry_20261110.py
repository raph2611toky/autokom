"""Offline security and functional tests for optional Artifact Registry promotion."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autokom_ha_registry', ROOT / 'scripts/ha/autokom_ha_registry.py')
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)
RID = 'f51c20ed-18b3-4f31-9c93-29d607a8ef02'


class RegistryTests(unittest.TestCase):
    def test_image_reference_is_only_autokom_controlled(self):
        self.assertEqual(registry.image_tag(registry.PROJECT, registry.REGION, registry.REPO, RID),
                         'us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images/'
                         'client-f51c20ed18b34f319c9329d607a8ef02:f51c20ed18b34f319c9329d607a8ef02')

    def test_untrusted_ids_are_rejected(self):
        for rid in ('../../etc/shadow', RID.upper(), 'foo', 'a'*128, RID + '/next'):
            with self.subTest(rid=rid):
                with self.assertRaises(registry.PromotionError):
                    registry.image_tag(registry.PROJECT, registry.REGION, registry.REPO, rid)

    def test_untrusted_projects_are_rejected(self):
        with self.assertRaises(registry.PromotionError):
            registry.identifiers('example.com', registry.REGION, registry.REPO)

    def test_archive_source_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'original.tar').write_bytes(b'X'*1024)
            (p/f'{RID}.tar').symlink_to('original.tar')
            with self.assertRaises(registry.PromotionError):
                registry.load_trusted_build(RID, p)

    def test_archive_small_is_refused_without_docker(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/f'{RID}.tar').write_bytes(b'bad')
            with self.assertRaises(registry.PromotionError):
                registry.load_trusted_build(RID, Path(d))

    def test_no_implicit_push_or_archive_import(self):
        with patch.object(registry, 'metadata_identity') as identity, patch.object(registry, 'metadata_token') as token:
            self.assertEqual(registry.main(['--publish', '--run-id', RID]), 2)
            identity.assert_not_called()
            token.assert_not_called()

    def test_check_is_read_only(self):
        with patch.object(registry, 'metadata_identity', return_value='vm@example.iam.gserviceaccount.com'), \
             patch.object(registry, 'metadata_token', return_value='not-the-real-token'), \
             patch.object(registry, 'check_repository') as check, \
             patch.object(registry, 'publish') as publish, \
             patch.object(registry, 'run_docker') as docker:
            self.assertEqual(registry.main(['--check']), 0)
            check.assert_called_once()
            publish.assert_not_called()
            docker.assert_not_called()

    def test_never_overwrites_existing_registry_manifest(self):
        with patch.object(registry.os, 'geteuid', return_value=0), \
             patch.object(registry, 'registry_manifest_digest', return_value='sha256:' + 'a'*64), \
             patch.object(registry, 'run_docker') as docker:
            with self.assertRaisesRegex(registry.PromotionError, 'déjà'):
                registry.publish(RID, registry.PROJECT, registry.REGION, registry.REPO,
                                 from_archive=False, identity='vm@example', token='secret')
            docker.assert_not_called()

    def test_post_push_uses_immutable_digest(self):
        pushed = 'sha256:' + 'a' * 64
        with patch.object(registry.os, 'geteuid', return_value=0), \
             patch.object(registry, 'registry_manifest_digest', side_effect=[None, pushed]), \
             patch.object(registry, 'run_docker', return_value='') as docker, \
             patch.object(registry.Path, 'is_socket', return_value=True):
            image = registry.publish(RID, registry.PROJECT, registry.REGION, registry.REPO,
                                     from_archive=False, identity='vm@example', token='some_token')
        self.assertTrue(image.endswith('@' + pushed))
        self.assertTrue(any('push' in call.args[0] for call in docker.call_args_list))

    def test_registry_404_means_image_missing(self):
        from urllib.error import HTTPError
        with patch.object(registry, 'urlopen', side_effect=HTTPError('test', 404, 'not found', {}, None)):
            self.assertIsNone(registry.registry_manifest_digest(
                registry.image_tag(registry.PROJECT, registry.REGION, registry.REPO, RID), 'token'))

    def test_registry_auth_denied_fails_closed(self):
        from urllib.error import HTTPError
        with patch.object(registry, 'urlopen', side_effect=HTTPError('test', 403, 'denied', {}, None)):
            with self.assertRaisesRegex(registry.PromotionError, 'HTTP_403'):
                registry.registry_manifest_digest(
                    registry.image_tag(registry.PROJECT, registry.REGION, registry.REPO, RID), 'token')


if __name__ == '__main__':
    unittest.main()
