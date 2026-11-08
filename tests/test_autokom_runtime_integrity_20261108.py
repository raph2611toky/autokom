"""Offline regression tests for scanner/domain runtime recovery. No system writes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from scripts.lib import autokom_runtime_integrity as ri


class IntegrityTests(unittest.TestCase):
    def test_health_requires_scanners_rules_and_both_watchers(self):
        self.assertTrue(ri.Integrity(True, True, True, True, True).ready)
        for kw in ('semgrep','trivy','rules','timer','path'):
            inputs=dict(semgrep=True,trivy=True,rules=True,timer=True,path=True)
            inputs[kw]=False
            self.assertFalse(ri.Integrity(**inputs).ready)

    def test_check_mode_never_changes_host(self):
        missing=ri.Integrity(True,False,False,False,False)
        with patch.object(ri,'inspect',return_value=missing),\
             patch.object(ri,'_repair_trivy') as apt,\
             patch.object(ri,'_ensure_units') as units,\
             patch.object(ri,'_ensure_rules') as rules:
            result=ri.reconcile(apply=False)
        self.assertEqual(result,missing)
        apt.assert_not_called();units.assert_not_called();rules.assert_not_called()

    def test_healthy_apply_is_noop(self):
        good=ri.Integrity(True,True,True,True,True)
        with patch.object(ri,'inspect',return_value=good),\
             patch.object(ri,'_repair_trivy') as apt,\
             patch.object(ri,'_ensure_units') as units,\
             patch.object(ri,'_ensure_rules') as rules:
            self.assertTrue(ri.reconcile(apply=True).ready)
        apt.assert_not_called();units.assert_not_called();rules.assert_not_called()

    def test_apply_repairs_three_gaps_independently(self):
        start=ri.Integrity(True,False,False,False,False)
        end=ri.Integrity(True,True,True,True,True)
        with patch.object(ri,'inspect',side_effect=[start,end]),\
             patch.object(ri.os,'geteuid',return_value=0),\
             patch.object(ri,'_repair_trivy') as apt,\
             patch.object(ri,'_ensure_units') as units,\
             patch.object(ri,'_ensure_rules',return_value=True) as rules:
            self.assertTrue(ri.reconcile(apply=True).ready)
        apt.assert_called_once();units.assert_called_once();rules.assert_called_once()

    def test_partial_failure_is_reported_even_when_other_repair_succeeds(self):
        bad=ri.Integrity(True,False,True,False,False)
        with patch.object(ri,'inspect',side_effect=[bad,bad]),\
             patch.object(ri.os,'geteuid',return_value=0),\
             patch.object(ri,'_repair_trivy',side_effect=ri.IntegrityError('paquet non conforme')),\
             patch.object(ri,'_ensure_units') as units:
            with self.assertRaisesRegex(ri.IntegrityError,'Trivy'):
                ri.reconcile(apply=True)
            units.assert_called_once()

    def test_nonroot_refuses_apply(self):
        bad=ri.Integrity(False,False,False,False,False)
        with patch.object(ri,'inspect',return_value=bad),patch.object(ri.os,'geteuid',return_value=1000):
            with self.assertRaisesRegex(ri.IntegrityError,'root'):
                ri.reconcile(apply=True)

    def test_semgrep_rules_installed_with_sane_permissions(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.yml';source.write_bytes(b'rules:\n'+b'x'*120)
            dest=root/'rules';target=dest/'client-semgrep-rules.yml'
            with patch.object(ri,'RULES_SOURCE',source),patch.object(ri,'RULES_DIRECTORY',dest),\
                 patch.object(ri,'RULES_TARGET',target):
                self.assertTrue(ri._ensure_rules())
                self.assertFalse(ri._ensure_rules())
                self.assertEqual(target.read_bytes(),source.read_bytes())
                self.assertEqual(target.stat().st_mode & 0o777,0o644)

    def test_semgrep_existing_drift_is_never_silently_overwritten(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.yml';source.write_bytes(b'rules:\n'+b'x'*120)
            dest=root/'rules';dest.mkdir();target=dest/'client-semgrep-rules.yml'
            target.write_text('custom local rules')
            with patch.object(ri,'RULES_SOURCE',source),patch.object(ri,'RULES_DIRECTORY',dest),\
                 patch.object(ri,'RULES_TARGET',target):
                with self.assertRaisesRegex(ri.IntegrityError,'différentes'):
                    ri._ensure_rules()
            self.assertEqual(target.read_text(),'custom local rules')

    def test_rule_symlink_rejected(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.yml';source.write_bytes(b'rules:\n'+b'x'*120)
            dest=root/'rules';dest.mkdir();target=dest/'client-semgrep-rules.yml'
            target.symlink_to(source)
            with patch.object(ri,'RULES_SOURCE',source),patch.object(ri,'RULES_DIRECTORY',dest),\
                 patch.object(ri,'RULES_TARGET',target):
                with self.assertRaisesRegex(ri.IntegrityError,'symbolique'):
                    ri._ensure_rules()

    def test_trivy_without_apt_package_has_no_install_side_effects(self):
        def run(argv,**kw):
            if argv[0]=='dpkg-query':return SimpleNamespace(returncode=1,stdout='',stderr='')
            self.fail('Unexpected mutating command: '+str(argv))
        with patch.object(ri,'_builder_binary',return_value=False),patch.object(ri,'_run',side_effect=run):
            with self.assertRaisesRegex(ri.IntegrityError,'absent d’APT'):
                ri._repair_trivy()

    def test_trivy_reinstalls_only_pinned_version(self):
        calls=[]
        def run(argv,**kw):
            calls.append(argv)
            if argv[:2]==['dpkg-query','-W']:
                return SimpleNamespace(returncode=0,stdout='0.76.2',stderr='')
            if argv[:2]==['dpkg-query','-S']:
                return SimpleNamespace(returncode=0,stdout='trivy: /usr/bin/trivy',stderr='')
            if argv[0]=='apt-get':
                return SimpleNamespace(returncode=0,stdout='',stderr='')
            self.fail('Unexpected command '+str(argv))
        with patch.object(ri,'_builder_binary',side_effect=[False,True]),\
             patch.object(ri.shutil,'which',return_value='/usr/bin/trivy'),\
             patch.object(ri,'_run',side_effect=run):
            ri._repair_trivy()
        self.assertEqual([x for x in calls if x[0]=='apt-get'][0][-1],'trivy=0.76.2')

    def test_systemd_install_does_not_overwrite_existing_units(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);units=root/'units';units.mkdir();templates=root/'templates';templates.mkdir()
            for unit in ri.DOMAIN_UNITS:
                (templates/(unit+'.example')).write_text('[Unit]\n[Service]\nExecStart=/bin/true\n')
            (units/ri.DOMAIN_UNITS[0]).write_text('custom service')
            active={u:False for u in ri.WATCHERS}
            def run(argv,**kw):
                if argv[:2]==['systemctl','enable']:
                    active[argv[-1]]=True
                return SimpleNamespace(returncode=0,stdout='')
            with patch.object(ri,'UNIT_DIRECTORY',units),patch.object(ri,'TEMPLATES',templates),\
                 patch.object(ri,'_run',side_effect=run),\
                 patch.object(ri,'_unit_active',side_effect=lambda unit:active[unit]):
                ri._ensure_units()
                self.assertEqual((units/ri.DOMAIN_UNITS[0]).read_text(),'custom service')
                self.assertTrue(all(active.values()))

    def test_rootless_execution_is_explicit_and_no_docker_socket(self):
        source=Path(ri.__file__).read_text()
        self.assertIn('runuser',source)
        self.assertIn('autokom-builder',source)
        self.assertNotIn('docker.sock',source)
        self.assertIn('systemctl',source)

    def test_exposed_runtime_cli_reconciles_integrity(self):
        src=(ri.ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        recover=(ri.ROOT/'scripts/lib/autokom_recover.py').read_text()
        self.assertIn('integrity_reconcile(apply=opts.apply)',src)
        self.assertIn('integrity_reconcile(apply=True)',recover)
        self.assertIn('failures.append(\'DevSecOps\')',src)


if __name__ == '__main__':
    unittest.main()
