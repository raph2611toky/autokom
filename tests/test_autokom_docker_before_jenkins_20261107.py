"""Regression GCP: Docker absent alors que Jenkins est partiellement installe."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import tempfile
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_cli as cli


class DockerBeforeJenkinsTests(unittest.TestCase):
    def test_docker_role_precedes_jenkins_role_in_services_playbook(self):
        content = (ROOT / 'ansible/playbooks/services.yml').read_text()
        self.assertLess(content.index('- role: docker_engine'), content.index('- role: jenkins_server'))
        self.assertLess(content.index('- role: jenkins_server'), content.index('- role: database_server'))

    def test_docker_is_part_of_foundation_before_services(self):
        play = (ROOT / 'ansible/playbooks/foundation.yml').read_text()
        self.assertIn('- role: docker_engine', play)
        self.assertLess(play.index('- role: security_cli'), play.index('- role: docker_engine'))
        self.assertEqual(cli.STAGES[0:2], ('foundation', 'services'))

    def test_docker_engine_is_signed_non_destructive_and_verifies_runtime(self):
        content = (ROOT / 'ansible/roles/docker_engine/tasks/main.yml').read_text()
        self.assertIn('https://download.docker.com/linux/ubuntu', content)
        self.assertIn('signed-by=/etc/apt/keyrings/autokom-docker.asc', content)
        self.assertIn('docker-ce-cli', content)
        self.assertIn('containerd.io', content)
        self.assertIn('docker-buildx-plugin', content)
        self.assertIn('docker-compose-plugin', content)
        self.assertIn('autokom_docker_cli.rc != 0', content)
        self.assertIn('not ansible_check_mode', content)
        self.assertIn('argv: [docker, info]', content)
        self.assertNotIn('state: absent', content)
        self.assertNotIn('state: restarted', content)
        self.assertNotIn('docker system prune', content)

    def test_partial_jenkins_detected_and_marked_complete(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            override = base / '20-autokom-loopback.conf'
            marker = base / 'services.complete'
            loc = base / '90-autokom-location.groovy'
            pending = lambda: cli.services_setup_pending(completed=marker, override=override, location=loc)
            self.assertFalse(pending())
            override.write_text('# Managed by AUTOKOM: phase-2\n[Service]\n')
            self.assertTrue(pending(), 'Jenkins installed by AUTOKOM before failure must resume')
            marker.write_text('AUTOKOM services phase complete\n')
            self.assertFalse(pending())
            marker.unlink()
            loc.write_text('// Managed by AUTOKOM: phase-2')
            self.assertFalse(pending(), 'Legacy completed production should be preserved')
            loc.unlink()
            override.write_text('# Managed by foreign installer\n')
            self.assertFalse(pending(), 'Foreign overrides must not be interpreted as AUTOKOM pending')

    def test_pending_stage_must_reapply_services_even_if_jenkins_is_active(self):
        state = cli.Snapshot(
            foundation_ok=True, jenkins_installed=True, jenkins_active=True,
            jenkins_private=True, jenkins_listening=True,
            jenkins_plugins_missing=(), prometheus_active=True,
            prometheus_private=True, grafana_active=True, grafana_private=True,
            metrics_up=True, datasource_ok=True, ufw_active=True,
            services_pending=True,
        )
        self.assertTrue(cli.actions(state)['services'])
        with patch.object(cli, 'call_module') as called, patch.object(cli, 'section'):
            cli.apply_stage('services', state, None)
        called.assert_called_once_with('scripts/install/services.sh', '--apply')

    def test_even_direct_services_reprovisions_docker_if_missing(self):
        state = cli.Snapshot(
            foundation_ok=False, jenkins_installed=True, jenkins_active=True,
            jenkins_private=True, jenkins_listening=True,
            jenkins_plugins_missing=(), prometheus_active=True,
            prometheus_private=True,
            grafana_active=True, grafana_private=True,
            metrics_up=True, datasource_ok=True, ufw_active=True,
            docker_ready=False,
        )
        self.assertTrue(cli.actions(state)['services'])
        with patch.object(cli, 'call_module') as called, patch.object(cli, 'section'):
            cli.apply_stage('services', state, None)
        called.assert_called_once_with('scripts/install/services.sh', '--apply')

    def test_full_install_resumes_partially_installed_jenkins_after_docker(self):
        initial = cli.Snapshot(
            foundation_ok=False, jenkins_installed=True, jenkins_active=True,
            jenkins_private=True, jenkins_listening=True,
            jenkins_plugins_missing=(), prometheus_active=True,
            prometheus_private=True, grafana_active=True, grafana_private=True,
            metrics_up=True, datasource_ok=True, ufw_active=True,
            docker_ready=False, services_pending=True,
        )
        current = [initial]
        visits = []

        def fake_apply(stage, state, cfg, **kwargs):
            visits.append(stage)
            if stage == 'foundation':
                current[0] = replace(current[0], foundation_ok=True, docker_ready=True)
            elif stage == 'services':
                current[0] = replace(current[0], services_pending=False)

        opts = SimpleNamespace(module=None, only=None, start_at=None,
                               backend=None, runtime=None, check=False)
        with patch.object(cli, 'snapshot', side_effect=lambda cfg: current[0]), \
             patch.object(cli, 'apply_stage', side_effect=fake_apply), \
             patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(cli, 'phase_progress'), \
             patch.object(cli, 'message'), \
             patch.object(cli, 'section'):
            self.assertEqual(cli.perform(opts, object()), 0)
        self.assertEqual(visits, ['foundation', 'services'])

    def test_completed_marker_created_only_after_all_provisioning_functions(self):
        source = (ROOT / 'scripts/lib/services.py').read_text()
        body = source[source.index('def main():'):source.index("if __name__ == '__main__':")]
        self.assertLess(body.index('ansible_install(args)'), body.index('configure_proxy(args)'))
        self.assertLess(body.index('configure_proxy(args)'), body.index("atomic_write(completed, 'AUTOKOM services phase complete"))


if __name__ == '__main__':
    unittest.main()
