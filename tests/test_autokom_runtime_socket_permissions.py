"""Regression coverage for the root:autokom broker directory boundary.

The live Unix permission test runs as an unprivileged uid, not as a mocked user.
"""
import importlib.util
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'scripts/systemd/autokom-platform-broker.service'
ROLE = ROOT / 'ansible/roles/platform_runtime/tasks/main.yml'

spec = importlib.util.spec_from_file_location('autokom_socket_broker_test', ROOT / 'scripts/lib/autokom_platform_broker.py')
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class SocketProvisioningTests(TestCase):
    def test_systemd_owns_runtime_directory_by_django_group(self):
        unit = UNIT.read_text()
        self.assertIn('User=root\nGroup=autokom\nRuntimeDirectory=autokom', unit)
        self.assertIn('RuntimeDirectoryMode=0750', unit)
        self.assertNotIn('Group=root', unit)
        self.assertNotIn('ExecStartPre=/usr/bin/chgrp', unit)

    def test_ansible_restores_only_narrow_directory_permissions(self):
        role = ROLE.read_text()
        self.assertIn('path: /run/autokom\n    state: directory\n    owner: root\n    group: autokom\n    mode: \'0750\'', role)
        self.assertNotIn('mode: \'0755\'\n\n- name: Installer le service root broker', role)

    def test_broker_refuses_untrusted_symlink_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'actual').mkdir()
            (root/'link').symlink_to(root/'actual',target_is_directory=True)
            with patch.object(broker, 'SOCKET', root/'link'/'platform-control.sock'):
                with self.assertRaises(broker.BrokerError):
                    broker._prepare_socket_directory()

    def test_doctor_reports_permission_and_protocol_separately(self):
        source = (ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        self.assertIn("'diagnostic=permission': 'Accès Unix refusé au compte Django.'", source)
        self.assertIn("'diagnostic=protocol': 'Réponse du broker invalide ou opération refusée.'", source)
        self.assertIn("['runuser','-u','autokom'", source)
        self.assertNotIn('print(str(exc))', source)

    @skipUnless(os.geteuid() == 0 and hasattr(os, 'setuid'), 'Test Unix réservé à root')
    def test_real_unprivileged_client_needs_directory_traverse(self):
        """The exact 0750 root:root failure is reproduced, then fixed."""
        try:
            target = pwd.getpwnam('nobody')
        except KeyError:
            self.skipTest('nobody is not installed')
        with tempfile.TemporaryDirectory(prefix='autokom-socket-test-') as td:
            root = Path(td)
            root.chmod(0o755)
            directory = root/'runtime'
            directory.mkdir(mode=0o750)
            sock_path = directory/'control.sock'
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(sock_path))
            listener.listen(1)
            try:
                sock_path.chmod(0o660)
                os.chown(sock_path, 0, target.pw_gid)
                script = ('import socket,sys\n'
                          's=socket.socket(socket.AF_UNIX)\n'
                          's.settimeout(2)\n'
                          's.connect(sys.argv[1])\n')
                def drop_privileges():
                    os.setgroups([])
                    os.setgid(target.pw_gid)
                    os.setuid(target.pw_uid)
                def client():
                    return subprocess.run([sys.executable, '-c', script, str(sock_path)],
                                          preexec_fn=drop_privileges, capture_output=True,
                                          text=True, timeout=4)
                self.assertNotEqual(client().returncode, 0, 'Before: root:root 0750 denies client')
                os.chown(directory, 0, target.pw_gid)
                self.assertEqual(client().returncode, 0, 'After: root:gid 0750 permits client')
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)
            finally:
                listener.close()
