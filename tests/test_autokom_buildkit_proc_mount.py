"""A rootless worker may exist while the first OCI RUN cannot mount procfs."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_buildkit_admin as admin


class BuildKitProcMountTests(unittest.TestCase):
    def test_rootless_service_does_not_overmount_proc_sys(self):
        unit = (ROOT/'scripts/systemd/autokom-buildkit.service').read_text()
        self.assertIn('ProtectKernelTunables=no', unit)
        self.assertNotIn('\nProtectKernelTunables=yes', unit)
        for expected in ('User=autokom-buildkit', 'ProtectSystem=full',
                         'ProtectKernelModules=true', 'ProtectControlGroups=true',
                         '--net=slirp4netns', '--disable-host-loopback'):
            self.assertIn(expected, unit)
        for forbidden in ('--oci-worker-no-process-sandbox', '--privileged',
                          '/var/run/docker.sock', 'tcp://'):
            self.assertNotIn(forbidden, unit)
        self.assertNotIn('kernel.apparmor_restrict_unprivileged_userns=0', unit)

    def test_provision_role_exercises_run_and_retains_isolation(self):
        role = (ROOT/'ansible/roles/buildkit_rootless/tasks/main.yml').read_text()
        self.assertIn('smoke_test()', role)
        self.assertIn('healthcheck()', role)
        self.assertIn('changed_when: false', role)
        self.assertIn('when: not ansible_check_mode', role)
        self.assertNotIn('systemctl stop apparmor', role)
        playbook = (ROOT/'ansible/playbooks/jenkins-agent.yml').read_text()
        self.assertIn('../roles/buildkit_rootless', playbook)
        doctor = (ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        self.assertIn('smoke_test()', doctor)

    def test_real_run_uses_runner_identity_and_no_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root/'workspace'
            base.mkdir(mode=0o700)
            bindir = root/'bin'
            bindir.mkdir()
            (bindir/'buildctl').write_text('mock')
            calls=[]
            def run(command, **kwargs):
                calls.append(command)
                self.assertEqual(command[:4], ['runuser','-u','autokom-builder','--'])
                self.assertIn('unix:///run/autokom-buildkit/buildkitd.sock', ' '.join(command))
                self.assertIn('--frontend', command)
                self.assertNotIn('--output', command)
                self.assertNotIn('docker', command)
                recipe=Path(next(x.split('=',1)[1] for x in command if x.startswith('dockerfile=')))/'Dockerfile'
                self.assertIn('RUN true',recipe.read_text())
                self.assertIn('sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662', recipe.read_text())
                return SimpleNamespace(returncode=0, stdout='done', stderr='')
            user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(admin,'BASE',bindir), \
                 patch.object(admin.pwd,'getpwnam',return_value=user,create=True) if hasattr(admin, 'pwd') else patch('pwd.getpwnam',return_value=user), \
                 patch.object(admin.subprocess,'run',side_effect=run):
                self.assertTrue(admin.smoke_test(workspace_base=base))
            self.assertEqual(len(calls),1)
            self.assertEqual(list(base.iterdir()),[])

    def test_proc_failure_gets_targeted_nonsecret_message(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); base=root/'workspace';base.mkdir(mode=0o700)
            bindir=root/'bin';bindir.mkdir();(bindir/'buildctl').write_text('mock')
            user=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            failure=SimpleNamespace(returncode=1,
                stdout='error mounting "proc" to rootfs at "/proc": operation not permitted',
                stderr='VFS: Mount too revealing')
            with patch.object(admin,'BASE',bindir), \
                 patch('pwd.getpwnam',return_value=user), \
                 patch.object(admin.subprocess,'run',return_value=failure):
                with self.assertRaises(admin.BuildkitError) as ctx:
                    admin.smoke_test(workspace_base=base)
            self.assertIn('ProtectKernelTunables', str(ctx.exception))
            self.assertNotIn('VFS: Mount too revealing', str(ctx.exception))

    def test_does_not_misreport_registry_failure_as_proc(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);base=root/'workspace';base.mkdir(mode=0o700)
            bindir=root/'bin';bindir.mkdir();(bindir/'buildctl').write_text('mock')
            user=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            failure=SimpleNamespace(returncode=1,stdout='',stderr='failed to resolve source metadata')
            with patch.object(admin,'BASE',bindir), \
                 patch('pwd.getpwnam',return_value=user), \
                 patch.object(admin.subprocess,'run',return_value=failure):
                with self.assertRaises(admin.BuildkitError) as ctx:admin.smoke_test(workspace_base=base)
            self.assertIn('image',str(ctx.exception).lower())
            self.assertNotIn('ProtectKernelTunables',str(ctx.exception))


if __name__=='__main__': unittest.main()
