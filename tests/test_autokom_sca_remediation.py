import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/runtime'))
import autokom_agent_local as runner
import autokom_sca_remediation as sca

ROW = ('CVE-2025-12345', 'example-pkg', '1.0.0', 'CRITICAL', ('1.0.1',))
BEFORE = (1, 0, {ROW})
CLEAN = (0, 0, set())


def populate(path):
    (path/'package.json').write_text('{"name":"sample","dependencies":{"example-pkg":"^1.0.0"}}')
    (path/'package-lock.json').write_text('{"lockfileVersion":3,"packages":{"":{},"node_modules/example-pkg":{"version":"1.0.0"}}}')


class ScaTests(unittest.TestCase):
    def test_scanner_ignores_raw_secrets_and_uses_bounded_fields(self):
        result = sca.findings({'SchemaVersion': 2, 'Results': [{
            'Vulnerabilities':[{'PkgName':'example-pkg','VulnerabilityID':'CVE-2025-12345','InstalledVersion':'1.0.0',
                                'Severity':'CRITICAL','FixedVersion':'1.0.1','Title':'private-marker'}],
            'Secrets':[{'Match':'do-not-leak'}]}]})
        self.assertEqual(result, (1,1,{ROW}))
        self.assertNotIn('do-not-leak', str(result))

    def test_repair_never_runs_customer_hooks_and_only_exports_lock(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp)
            populate(source)
            def execute(args, timeout):
                context = Path(next(x.split('=',1)[1] for x in args if x.startswith('context=')))
                self.assertEqual({p.name for p in context.iterdir()}, {'package.json','package-lock.json','Dockerfile'})
                content = (context/'Dockerfile').read_text()
                self.assertIn('--ignore-scripts', content)
                self.assertNotIn('--force', content)
                output = Path(next(x.split('dest=')[1] for x in args if x.startswith('type=local')))
                output.mkdir()
                (output/'package-lock.json').write_text((source/'package-lock.json').read_text().replace('"version":"1.0.0"', '"version":"1.0.1"'))
            self.assertTrue(sca.repair_lock(source,source,execute))
            self.assertIn('"version":"1.0.1"', (source/'package-lock.json').read_text())

    def run_sca(self, final=None, *, initial=BEFORE, method='STATIC', send_error=None):
        doc = {'build':{'method':method,'profile':'vite'}, 'sast_reports':True}
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            populate(path)
            lock = path/'package-lock.json'
            original = lock.read_bytes()
            def repair(source, dest, execute, **kw):
                p = dest/'package-lock.json'
                p.write_text(p.read_text().replace('"version":"1.0.0"', '"version":"1.0.1"'))
                return True
            with patch.object(sca,'scan_tree',side_effect=[initial,final,final]) as scan, patch.object(sca,'repair_lock',side_effect=repair) as fix, patch.object(runner,'buildkit_preflight'), patch.object(runner,'send_security_report',side_effect=send_error) as send:
                try:
                    runner.security_scan_dependencies(doc,path)
                    passed = True
                except SystemExit:
                    passed = False
                return passed, lock.read_bytes(), original, scan.call_count, fix.call_count, send.call_args.args[2]

    def test_success_only_after_clean_rescan_and_signed_report(self):
        passed, lock, original, scans, fixes, report = self.run_sca(CLEAN)
        self.assertTrue(passed)
        self.assertNotEqual(lock, original)
        self.assertEqual((scans, fixes),(2,1))
        self.assertTrue(report['remediated'])
        self.assertEqual(report['lock_sha256'], hashlib.sha256(lock).hexdigest())
        self.assertEqual(report['findings'][0]['applied_versions'], ['1.0.1'])

    def test_remaining_findings_restore_original_lock(self):
        passed, lock, original, _, _, report = self.run_sca(BEFORE)
        self.assertFalse(passed)
        self.assertEqual(lock, original)
        self.assertEqual(report['remaining'], 1)

    def test_secrets_and_custom_recipes_are_never_repaired(self):
        for initial, method in [((1,1,{ROW}), 'STATIC'), (BEFORE, 'DOCKERFILE')]:
            passed, lock, original, scans, fixes, _ = self.run_sca(initial=initial,method=method)
            self.assertFalse(passed)
            self.assertEqual((lock,scans,fixes),(original,1,0))

    def test_no_publication_if_corrected_report_cannot_be_recorded(self):
        self.assertFalse(self.run_sca(CLEAN,send_error=OSError('offline'))[0])

    def test_malformed_json_fields_cannot_prove_scan_passed(self):
        for payload in ({}, {'SchemaVersion':2,'Results':{}}, {'SchemaVersion':2,'Results':[None]}):
            with self.assertRaises((ValueError,AttributeError)):
                sca.findings(payload)


if __name__ == '__main__':
    unittest.main()
