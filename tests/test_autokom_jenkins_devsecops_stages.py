from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts/runtime'), str(ROOT/'scripts/lib')]
import autokom_sca_remediation as sca
import autokom_agent_local as runner
import autokom_sast_report as sast
import autokom_platform_broker as broker

RID = '84bf09ac-1bae-4027-a187-7e9a979e9fda'
LONG_RANGE = ('>=2.8.0 || >= 3.2.0-dev || >= 3.3.0-dev || >= 3.4.0-dev || >= 3.5.0-dev || '
              '>= 3.6.0-dev || >= 3.6.0-beta || >= 3.7.0-dev || >= 3.7.0-beta')


class ScaInputTests(unittest.TestCase):
    def test_cra_typescript_range_and_registry_alias_pass(self):
        self.assertGreater(len(LONG_RANGE),120)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path/'package.json').write_text('{}')
            (path/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{
                '':{},'node_modules/tsutils':{'peerDependencies':{'typescript':LONG_RANGE}},
                'node_modules/deps':{'dependencies':{'some-alias':'npm:@example/pkg@^3.2.0'}}}}))
            self.assertEqual(sca.validate_npm(path),{})

    def test_external_dependencies_still_rejected(self):
        for spec in ('file:../elsewhere','github:user/repo','git+https://example.invalid/x',
                     'https://example.invalid/x.tgz','npm:foo@file:../x','../path','@user/repo'):
            self.assertFalse(sca.registry_spec(spec), spec)


class StagesTests(unittest.TestCase):
    def test_jenkins_separates_and_conditions_real_checks(self):
        for rel in ('scripts/runtime/Jenkinsfile.autokom-local','scripts/preprod/Jenkinsfile'):
            pipeline = (ROOT/rel).read_text()
            for name, op in [('SAST Semgrep','sast-semgrep'), ('Analyse IA - DeepSAST','deepsast'),
                             ('SCA Trivy','sca'), ('DAST - contrôle HTTP privé','dast')]:
                stage = pipeline.split(f"stage('{name}')",1)[1].split("stage('",1)[0]
                self.assertIn("value: 'DEVSECOPS'", stage)
                self.assertIn(f"autokom_agent_local.py {op}'", stage)
            self.assertLess(pipeline.index("stage('BuildKit"), pipeline.index("stage('DAST"))

    def test_semgrep_verdict_survives_advisory_stage(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            doc = {'run_id':RID,'sast_reports':True}
            with patch.object(runner,'source',return_value=path), patch.object(runner,'stage') as stage, \
                 patch.object(sast,'scan_semgrep',return_value=({'count':1,'rules':{'safe':1},
                     'locations':[{'path':'private.py'}]},1)), \
                 patch.object(sast,'infer_locations',return_value={'status':'COMPLETE','analyzed':1}), \
                 patch.object(runner,'send_security_report') as send:
                runner.split_semgrep(doc)
                self.assertEqual(runner.split_deepsast(doc),(1,'SUCCESS'))
            self.assertFalse(runner._sast_checkpoint(doc,path).exists())
            self.assertEqual(stage.call_args_list[1].args,('sast','FAILED'))
            self.assertEqual(stage.call_args_list[-1].args,('deepsast','SUCCESS'))
            self.assertNotIn('locations',send.call_args.args[2])

    def test_dast_deferred_is_not_success_and_devops_has_no_stage(self):
        with patch.object(runner,'send_security_report',return_value='JENKINS_DAST_DEFERRED'), \
             patch.object(runner,'stage') as stage:
            with self.assertRaises(SystemExit) as raised:
                runner.jenkins_dast({'run_id':RID})
            self.assertEqual(raised.exception.code,12)
            self.assertEqual(stage.call_args.args,('dast','SKIPPED'))

    def test_probe_has_no_ports_mounts_or_environment_and_cleanup_is_owned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/(RID+'.tar')).write_bytes(b'x'*1024)
            def execute(argv, **kw):
                if 'load' in argv:
                    return ''
                if 'image' in argv:
                    return 'sha256:'+'b'*64
                if 'container:' in ' '.join(argv):
                    compile(kw['input'],'<probe>','exec')
                    return '{"http":200,"warnings":[]}'
                return ''
            with patch.object(broker,'IMAGES',root),patch.object(broker,'run',side_effect=execute) as run, \
                 patch.object(broker.subprocess,'run',return_value=Mock(returncode=0)):
                self.assertEqual(broker._dynamic_preview({'run_id':RID,'internal_port':8080,'healthcheck':'/',
                    'memory_mb':256,'cpu_millicores':200}), {'http':200,'warnings':[]})
            commands=[call.args[0] for call in run.call_args_list]
            candidate=next(c for c in commands if c[:3]==['docker','run','-d'])
            self.assertEqual(candidate[candidate.index('--network')+1],'none')
            for flag in ('-p','--privileged','--volume','--env','--env-file'):
                self.assertNotIn(flag,candidate)
            self.assertEqual(len([c for c in commands if c[:3]==['docker','rm','-f']]),2)


if __name__ == '__main__':
    unittest.main()
