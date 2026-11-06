"""Regression and security checks for isolated AUTOKOM BuildKit installation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('autokom_subids',
    ROOT / 'scripts/lib/autokom_buildkit_subids.py')
subid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subid)


class ProvisioningTests(unittest.TestCase):
    def test_allocates_non_overlapping_subuids_gids(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target/'subuid').write_text('someone:500000:65536\n', encoding='ascii')
            (target/'subgid').write_text('someone:500000:65536\n', encoding='ascii')
            self.assertTrue(subid.allocate('autokom-buildkit', target))
            self.assertFalse(subid.allocate('autokom-buildkit', target))
            self.assertIn('autokom-buildkit:565536:65536', (target/'subuid').read_text())
            self.assertIn('autokom-buildkit:565536:65536', (target/'subgid').read_text())

    def test_existing_ranges_preserved_and_corrupt_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target/'subuid').write_text('autokom-buildkit:600000:65536\n', encoding='ascii')
            (target/'subgid').write_text('someone:500000:65536\n', encoding='ascii')
            self.assertTrue(subid.allocate('autokom-buildkit', target))
            self.assertIn('autokom-buildkit:600000:65536', (target/'subuid').read_text())
            (target/'subuid').write_text('autokom-buildkit:123:8\n', encoding='ascii')
            with self.assertRaises(ValueError):
                subid.allocate('autokom-buildkit', target)

    def test_pinned_official_checksums_and_version(self):
        yml = (ROOT/'ansible/roles/buildkit_rootless/tasks/main.yml').read_text()
        self.assertIn('v0.34.2', yml)
        self.assertIn('b9f18b8e37eb450e9e5df03117045237cdd12d3c4a0034ffeeaa857852a15e13', yml)
        self.assertIn('a4ad483a33b2f1544c5ccce1c5956c559e1a6e84b8bf6cedd847d695885237a3', yml)
        self.assertIn('checksum:', yml)
        self.assertNotIn('state: latest', yml)

    def test_agent_automatically_provisions_buildkit(self):
        yml = (ROOT/'ansible/playbooks/jenkins-agent.yml').read_text()
        self.assertIn('../roles/jenkins_agent', yml)
        self.assertIn('../roles/buildkit_rootless', yml)
        agent = (ROOT/'scripts/lib/autokom_jenkins_agent.py').read_text()
        self.assertIn('healthcheck()', agent)
        self.assertIn('timeout=900', agent)

    def test_systemd_hardening_and_no_docker_socket(self):
        daemon = (ROOT/'scripts/systemd/autokom-buildkit.service').read_text()
        agent = (ROOT/'scripts/systemd/autokom-jenkins-agent.service').read_text()
        self.assertIn('User=autokom-buildkit', daemon)
        self.assertIn('--net=slirp4netns', daemon)
        self.assertIn('--disable-host-loopback', daemon)
        self.assertIn('autokom_buildkit_socket_permissions.py', daemon)
        self.assertIn('--oci-worker-snapshotter=native', daemon)
        self.assertIn('BUILDKIT_HOST=unix:///run/autokom-buildkit/buildkitd.sock', agent)
        self.assertIn('Requires=jenkins.service autokom-buildkit.service', agent)
        self.assertNotIn('/var/run/docker.sock', daemon+agent)
        self.assertNotIn('tcp://', daemon+agent)
        self.assertNotIn('--privileged', daemon+agent)

    def test_runtime_command_and_doctor(self):
        cli = (ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        self.assertIn("sub.add_parser('buildkit'", cli)
        self.assertIn('healthcheck()', cli)
        provisioner = (ROOT/'scripts/lib/autokom_buildkit_admin.py').read_text()
        self.assertIn("'--syntax-check'", provisioner)
        self.assertIn("'debug', 'workers'", provisioner)
        self.assertIn('BUILDKIT_HOST=', provisioner)

    def test_existing_runner_accepts_only_private_buildkit_socket(self):
        runner = (ROOT/'scripts/runtime/autokom_agent_local.py').read_text()
        self.assertIn("allowed = Path('/run/autokom-buildkit/buildkitd.sock')", runner)
        self.assertNotIn("Path('/run/user') / str(os.getuid())", runner)


if __name__ == '__main__':
    unittest.main()
