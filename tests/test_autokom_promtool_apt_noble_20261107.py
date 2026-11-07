#!/usr/bin/env python3
"""Regression: Ubuntu Noble embeds promtool inside prometheus (not a .deb)."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import observability as o


class PromtoolPackaging(unittest.TestCase):
    def test_apt_lookup_detects_separate_promtool_package(self):
        with patch.object(o.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Package: promtool\n')) as sub:
            self.assertTrue(o.apt_package_available('promtool'))
        sub.assert_called_once_with(['apt-cache', 'show', '--no-all-versions', 'promtool'],
                                    capture_output=True, text=True, timeout=20, check=False)

    def test_apt_lookup_does_not_invent_package(self):
        with patch.object(o.subprocess, 'run', return_value=SimpleNamespace(returncode=100, stdout='')):
            self.assertFalse(o.apt_package_available('promtool'))
        with patch.object(o.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')):
            self.assertFalse(o.apt_package_available('promtool'))

    def test_apt_lookup_failure_reports_useful_error(self):
        with patch.object(o.subprocess, 'run', side_effect=FileNotFoundError('apt-cache')):
            with self.assertRaisesRegex(o.SetupError, 'catalogue APT'):
                o.apt_package_available('promtool')

    def install(self, *, prometheus_present=False, tool_sequence=('', '/usr/bin/promtool'), separate=False):
        actions = []
        def run(args, **kwargs):
            actions.append(tuple(args))
            return ''
        def ready(*args, **kwargs):
            actions.append(('wait-ready',))
            return ['127.0.0.1:9090']
        with patch.object(o, 'package_installed', return_value=prometheus_present), \
             patch.object(o, 'run', side_effect=run), \
             patch.object(o, 'owned_write', return_value=True), \
             patch.object(o, 'ensure_prometheus_storage'), \
             patch.object(o, 'validate_prom_config'), \
             patch.object(o, 'wait_service_ready', side_effect=ready), \
             patch.object(o, 'PROM_CONFIG', MagicMock(is_file=MagicMock(return_value=False))), \
             patch.object(o, 'PROM_DROPIN', MagicMock(exists=MagicMock(return_value=False))), \
             patch.object(o, 'active', return_value=False), \
             patch.object(o.shutil, 'which', side_effect=list(tool_sequence)), \
             patch.object(o, 'apt_package_available', return_value=separate) as lookup, \
             patch.object(o, 'autokom_banner'), patch.object(o, 'out'):
            o.ensure_prometheus(SimpleNamespace(mode='apply', non_interactive=True,
                                                prometheus_domain=''))
        return actions, lookup.call_args_list

    def test_ubuntu_2404_installs_only_prometheus_package(self):
        actions, checks = self.install(separate=False)
        self.assertIn(('apt-get', 'update', '-qq'), actions)
        self.assertIn(('apt-get', 'install', '-y', '--no-install-recommends', 'prometheus'), actions)
        self.assertNotIn(('apt-get', 'install', '-y', '--no-install-recommends', 'prometheus', 'promtool'), actions)
        self.assertEqual(str(checks[0]), "call('promtool')")
        self.assertIn(('wait-ready',), actions)

    def test_newer_ubuntu_with_split_package_installs_both(self):
        actions, _ = self.install(separate=True)
        self.assertIn(('apt-get', 'install', '-y', '--no-install-recommends', 'prometheus', 'promtool'), actions)

    def test_existing_prometheus_missing_tool_installs_only_tool_if_available(self):
        actions, _ = self.install(prometheus_present=True, separate=True)
        self.assertIn(('apt-get', 'install', '-y', '--no-install-recommends', 'promtool'), actions)
        self.assertNotIn(('apt-get', 'install', '-y', '--no-install-recommends', 'prometheus'), actions)

    def test_already_healthy_installation_does_not_run_apt(self):
        actions, checks = self.install(prometheus_present=True,
                                       tool_sequence=('/usr/bin/promtool', '/usr/bin/promtool'), separate=True)
        self.assertFalse(any(x[0] == 'apt-get' for x in actions))
        self.assertFalse(checks)

    def test_fail_closed_when_no_promtool_binary_after_install(self):
        with patch.object(o, 'package_installed', return_value=False), \
             patch.object(o, 'run', return_value=''), \
             patch.object(o, 'owned_write', return_value=True), \
             patch.object(o, 'ensure_prometheus_storage') as storage, \
             patch.object(o, 'PROM_DROPIN', MagicMock(exists=MagicMock(return_value=False))), \
             patch.object(o, 'active', return_value=False), \
             patch.object(o.shutil, 'which', return_value=None), \
             patch.object(o, 'apt_package_available', return_value=False), \
             patch.object(o, 'autokom_banner'), patch.object(o, 'out'):
            with self.assertRaisesRegex(o.SetupError, 'promtool introuvable'):
                o.ensure_prometheus(SimpleNamespace(mode='apply', non_interactive=True,
                                                     prometheus_domain=''))
        storage.assert_not_called()

    def test_check_mode_never_installs_package(self):
        with patch.object(o, 'package_installed', return_value=False), \
             patch.object(o, 'run') as run, patch.object(o, 'out'), \
             patch.object(o, 'autokom_banner'):
            o.ensure_prometheus(SimpleNamespace(mode='check'))
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
