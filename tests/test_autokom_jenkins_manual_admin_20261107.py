"""User-defined first Jenkins admin without a browser or generated credentials."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sp = importlib.util.spec_from_file_location('admin_selection_test_module', ROOT/'scripts/lib/jenkins_firstboot.py')
b = importlib.util.module_from_spec(sp); sp.loader.exec_module(b)
sp2 = importlib.util.spec_from_file_location('postinstall_selection_test_module', ROOT/'scripts/lib/jenkins_postinstall.py')
p = importlib.util.module_from_spec(sp2); sp2.loader.exec_module(p)

class AdminChoices(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root/'jenkins'; self.home.mkdir()
        self.secrets = self.root/'secrets'; self.secrets.mkdir(mode=0o700)
        self.uid = os.getuid(); self.gid = os.getgid()
        for name, path in {
            'JENKINS_HOME':self.home, 'SECRETS':self.secrets,
            'PASSWORD':self.secrets/'jenkins-first-admin.password',
            'PROFILE':self.secrets/'jenkins-first-admin.profile.json',
            'OLD_PASSWORD':self.secrets/'jenkins-first-admin.password.previous-auto',
            'AUTH':self.secrets/'jenkins-admin.auth',
            'STAGING':self.home/'.autokom-firstboot',
            'SEED':self.home/'.autokom-firstboot/admin.seed',
            'TOKEN':self.home/'.autokom-firstboot/admin.auth.pending',
            'SCRIPT':self.home/'init.groovy.d/95-autokom-first-admin.groovy',
            'MATRIX_SCRIPT':self.home/'init.groovy.d/96-autokom-first-admin-matrix.groovy',
            'MATRIX_PENDING':self.home/'.autokom-firstboot/matrix.pending',
            'MATRIX_DONE':self.home/'.autokom-firstboot/matrix.done',
            'ADMIN_MAIL_PROFILE':self.home/'.autokom-firstboot/admin.profile',
        }.items():
            patch=mock.patch.object(b,name,path); patch.start(); self.addCleanup(patch.stop)
        pp=mock.patch.object(b.pwd,'getpwnam', return_value=SimpleNamespace(pw_uid=self.uid,pw_gid=self.gid))
        pp.start();self.addCleanup(pp.stop)

    def test_operator_defined_identity_is_private_and_staged(self):
        selected=b.save_admin_choice('chef-ikom','Responsable SecOps IKOM','secops@ikom.mg','M0tDePasse!Serveur#123')
        self.assertEqual(selected['username'],'chef-ikom')
        self.assertNotIn('M0tDePasse',b.PROFILE.read_text())
        self.assertEqual(b.PROFILE.stat().st_mode & 0o777,0o600)
        self.assertEqual(b.PASSWORD.stat().st_mode & 0o777,0o600)
        b._stage()
        self.assertEqual(b.SEED.read_text().splitlines(),['chef-ikom','M0tDePasse!Serveur#123','Responsable SecOps IKOM','secops@ikom.mg'])
        self.assertEqual(b.admin_username(),'chef-ikom')

    def test_reuse_without_second_question(self):
        b.save_admin_choice('chef-ikom','Responsable SecOps IKOM','secops@ikom.mg','M0tDePasse!Serveur#123')
        with mock.patch.object(p,'selected_admin',return_value=b.selected_admin()), mock.patch.object(p,'ask') as ask, mock.patch.object(p,'read_masked') as masked:
            self.assertEqual(p.choose_initial_administrator()['username'],'chef-ikom')
            ask.assert_not_called();masked.assert_not_called()

    def test_interactive_prompts_before_restart(self):
        selections=['chef-ikom','Responsable SecOps IKOM','secops@ikom.mg']
        with mock.patch.object(p,'selected_admin', side_effect=[None]), \
             mock.patch.object(p,'ask',side_effect=selections), \
             mock.patch.object(p,'read_masked',side_effect=['M0tDePasse!Serveur#123']*2), \
             mock.patch.object(p,'save_admin_choice',return_value={'username':'chef-ikom'}) as save:
            p.choose_initial_administrator()
            save.assert_called_once_with(*selections,'M0tDePasse!Serveur#123')

    def test_legacy_password_is_preserved_with_explicit_new_choice(self):
        b._stage()   # prior AUTOKOM auto-generated attempt
        old=b.PASSWORD.read_text()
        b.save_admin_choice('chef-ikom','Responsable SecOps IKOM','secops@ikom.mg','M0tDePasse!Serveur#123')
        self.assertEqual(b.OLD_PASSWORD.read_text(),old)
        self.assertFalse(b.SEED.exists())
        b._stage()
        self.assertTrue(b.SEED.read_text().startswith('chef-ikom\n'))

    def test_does_not_overwrite_controller_with_pending_token(self):
        b.STAGING.mkdir()
        b.TOKEN.write_text('x')
        with self.assertRaisesRegex(RuntimeError,'en cours'):
            b.save_admin_choice('chef-ikom','Responsable SecOps IKOM','secops@ikom.mg','M0tDePasse!Serveur#123')
        self.assertFalse(b.PROFILE.exists())

    def test_no_unprompted_auto_default_and_validation(self):
        (self.home/'secrets').mkdir()
        (self.home/'secrets/initialAdminPassword').write_text('temporary')
        with self.assertRaisesRegex(RuntimeError, 'définir le premier administrateur'):
            b.bootstrap(approved=True)
        for username,email,password in [('admin','secops@ikom.mg','a'*20),('chef-ikom','no-at-sign','a'*20),('chef-ikom','secops@ikom.mg','weak')]:
            with self.assertRaises(ValueError):
                b.save_admin_choice(username,'Responsable SecOps',email,password)
        self.assertFalse(b.PASSWORD.exists())

    def test_plugin_choices_are_reused_without_prompts(self):
        selection=self.root/'install/jenkins-plugins-selection.json'
        with mock.patch.object(p,'PLUGIN_SELECTION',selection):
            args=p.parse(['--apply','--interactive'])
            args.profile='devsecops'; args.multibranch_scan=True; args.harden=True
            args.controller_zero=False; args.disable_inbound_agents=False
            p._save_plugin_selection(args)
            self.assertEqual(selection.stat().st_mode & 0o777,0o600)
            self.assertNotIn('password',selection.read_text())
            next_args=p.parse(['--apply','--interactive'])
            with mock.patch.object(p,'pick') as pick:
                p.interactive_configure(next_args)
                pick.assert_not_called()
            self.assertEqual(next_args.profile,'devsecops')
            self.assertTrue(next_args.multibranch_scan)

    def test_explicit_reconfigure_ignores_previous_plugin_selections(self):
        selection=self.root/'install/jenkins-plugins-selection.json'
        with mock.patch.object(p,'PLUGIN_SELECTION',selection):
            args=p.parse(['--apply','--interactive'])
            args.profile='baseline'; args.multibranch_scan=False
            args.harden=True; args.controller_zero=False; args.disable_inbound_agents=False
            p._save_plugin_selection(args)
            new_args=p.parse(['--apply','--interactive','--reconfigure'])
            with mock.patch.object(p,'pick',return_value=1) as pick, \
                 mock.patch.object(p,'choose_optional_multibranch',return_value=new_args):
                p.interactive_configure(new_args)
            pick.assert_called_once()
            self.assertEqual(p._load_plugin_selection()['profile'],'devsecops')

    def test_groovy_uses_operator_identity_and_mailer_only_after_plugin(self):
        initial=b.TEMPLATE.read_text(); matrix=b.MATRIX_TEMPLATE.read_text()
        self.assertIn('realm.createAccount(pair[0], pair[1])',initial)
        self.assertIn('person.setFullName(pair[2])',initial)
        self.assertIn('strategy.add(Jenkins.ADMINISTER, adminUser)',matrix)
        self.assertIn('adminProfile.readLines',matrix)
        self.assertIn('Mailer$UserProperty',matrix)
        self.assertIn('mailer', (ROOT/'config/jenkins/plugins-baseline.txt').read_text())

if __name__=='__main__': unittest.main()
