"""Regression: legacy services state mode, firstboot hook and account recovery."""
import importlib.util
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT/relative)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

perms = load('autokom_install_permissions_recovery_test', 'scripts/lib/autokom_install_permissions.py')
first = load('autokom_firstboot_recovery_test', 'scripts/lib/jenkins_firstboot.py')
post = load('autokom_postinstall_recovery_test', 'scripts/lib/jenkins_postinstall.py')
services = load('autokom_services_recovery_test', 'scripts/lib/services.py')

class PrivateStateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_services_legacy_0755_mode_repaired(self):
        path = self.root/'install'; path.mkdir(mode=0o755); path.chmod(0o755)
        self.assertEqual(perms.private_install_directory(path), path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(perms.private_install_directory(path), path)

    def test_world_writable_not_silently_trusted(self):
        path=self.root/'install'; path.mkdir(); path.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError,'écriture groupe/autres'):
            perms.private_install_directory(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o777)

    def test_group_writable_not_silently_trusted(self):
        path=self.root/'install';path.mkdir();path.chmod(0o770)
        with self.assertRaisesRegex(RuntimeError,'écriture groupe/autres'):
            perms.private_install_directory(path)

    def test_symlink_rejected(self):
        valid=self.root/'actual';valid.mkdir()
        (self.root/'alias').symlink_to(valid)
        with self.assertRaisesRegex(RuntimeError,'lien symbolique'):
            perms.private_install_directory(self.root/'alias')

    def test_foreign_owner_rejected(self):
        path=self.root/'install';path.mkdir(mode=0o700)
        orig_fstat=perms.os.fstat
        def altered(fd):
            st = orig_fstat(fd)
            class Fake:
                st_mode=st.st_mode
                st_uid=st.st_uid+1
            return Fake()
        with mock.patch.object(perms.os,'fstat',side_effect=altered):
            with self.assertRaisesRegex(RuntimeError,'propriétaire inattendu'):
                perms.private_install_directory(path)

    def test_plugins_recovers_after_services_wrote_mode_755(self):
        d=self.root/'install';d.mkdir();d.chmod(0o755)
        file=d/'jenkins-plugins-selection.json'
        args=post.parse(['--apply','--interactive'])
        args.profile='devsecops';args.multibranch_scan=True;args.harden=True
        args.controller_zero=False;args.disable_inbound_agents=False
        with mock.patch.object(post, 'PLUGIN_SELECTION', file):
            post._save_plugin_selection(args)
            self.assertEqual(stat.S_IMODE(d.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
            self.assertEqual(post._load_plugin_selection()['profile'],'devsecops')

    def test_services_selection_and_marker_create_private_state(self):
        install=self.root/'install'; install.mkdir(mode=0o755);install.chmod(0o755)
        args=SimpleNamespace(**dict.fromkeys(services.SAVED_CHOICES,None))
        args.db='mysql'; args.domain='jenkins.ikom.mg'
        services.save_choices(args,path=install/'services-selection.json')
        services.mark_incomplete(path=install/'services.incomplete')
        self.assertEqual(stat.S_IMODE(install.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((install/'services-selection.json').stat().st_mode),0o600)

class SafeLegacyAdminTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.home=self.root/'jenkins';self.home.mkdir()
        self.secrets=self.root/'secrets';self.secrets.mkdir(mode=0o700)
        mapping={
            'JENKINS_HOME':self.home,'SECRETS':self.secrets,
            'PASSWORD':self.secrets/'jenkins-first-admin.password',
            'OLD_PASSWORD':self.secrets/'jenkins-first-admin.password.previous-auto',
            'PROFILE':self.secrets/'jenkins-first-admin.profile.json',
            'AUTH':self.secrets/'jenkins-admin.auth',
            'STAGING':self.home/'.autokom-firstboot',
            'SEED':self.home/'.autokom-firstboot/admin.seed',
            'TOKEN':self.home/'.autokom-firstboot/admin.auth.pending',
            'LEGACY_PROOF':self.home/'.autokom-firstboot/legacy.auto-credential',
            'LEGACY_PRESERVED':self.home/'.autokom-firstboot/legacy.preserved',
            'SCRIPT':self.home/'init.groovy.d/95-autokom-first-admin.groovy',
        }
        for key,val in mapping.items():
            patch=mock.patch.object(first,key,val);patch.start();self.addCleanup(patch.stop)
        user=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
        patch=mock.patch.object(first.pwd,'getpwnam',return_value=user);patch.start();self.addCleanup(patch.stop)

    def test_e39c_bootstrap_hook_is_recognized_exactly(self):
        old = (ROOT/'tests/fixtures/autokom_firstboot_e39c_legacy.groovy')
        self.assertTrue(old.exists())
        first.SCRIPT.parent.mkdir(parents=True)
        first.SCRIPT.write_bytes(old.read_bytes()); first.SCRIPT.chmod(0o640)
        first.PASSWORD.write_text('a'*40+'\n');first.PASSWORD.chmod(0o600)
        self.assertTrue(first.known_bootstrap_hook())
        self.assertTrue(first.interrupted_managed_bootstrap())
        first.save_admin_choice('nouvel-admin', 'Administrateur','admin@ikom.mg','strong-password-12345678')
        first._stage()
        self.assertEqual(first.LEGACY_PROOF.read_text(),'autokom-admin\n'+'a'*40+'\n')
        self.assertEqual(first.SCRIPT.read_text(),first.TEMPLATE.read_text())
        backup=self.secrets/('jenkins-bootstrap-hook-'+ hashlib.sha256(old.read_bytes()).hexdigest()[:16]+'.backup')
        self.assertEqual(backup.read_bytes(),old.read_bytes())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode),0o600)
        first._stage() # Repeat must be idempotent

    def test_unknown_hook_is_preserved(self):
        first.SCRIPT.parent.mkdir(parents=True)
        first.SCRIPT.write_text('malicious or foreign hook')
        with self.assertRaisesRegex(RuntimeError,'étranger'):
            first.save_admin_choice('nouvel-admin','Administrateur','admin@ikom.mg','strong-password-12345678')
        self.assertEqual(first.SCRIPT.read_text(),'malicious or foreign hook')

    def test_old_password_not_replaced_implicitly(self):
        first.PASSWORD.write_text('a'*40+'\n')
        first.PASSWORD.chmod(0o600)
        first.save_admin_choice('nouvel-admin','Administrateur','admin@ikom.mg','strong-password-12345678')
        self.assertEqual(first.OLD_PASSWORD.read_text(),'a'*40+'\n')
        self.assertNotEqual(first.PASSWORD.read_text(),first.OLD_PASSWORD.read_text())

    def test_groovy_preserves_legacy_only_after_password_proof(self):
        initial=first.TEMPLATE.read_text()
        matrix=first.MATRIX_TEMPLATE.read_text()
        self.assertIn('detail.isPasswordCorrect(prior[1])',initial)
        self.assertIn('realm.getAllUsers()',initial)
        self.assertIn('existing configured accounts not managed',initial)
        self.assertIn("strategy.add(Jenkins.ADMINISTER, 'autokom-admin')",matrix)
        self.assertIn('getAllUsers()',matrix)
        self.assertNotIn('User.getAll().findAll',initial)

if __name__=='__main__':unittest.main()
