"""Security pipeline and dynamic domain metadata regression tests."""
import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
spec=importlib.util.spec_from_file_location('autokom_agent_devsecops_probe',ROOT/'scripts/runtime/autokom_agent_local.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
from autokom_platform_broker import _domain_config_check

class DevSecOpsContract(TestCase):
    def test_devops_deliberately_skips_scanners(self):
        seen=[]
        with patch.object(agent,'stage',side_effect=lambda *args:seen.append(args)),patch.object(agent,'source',side_effect=AssertionError('source should not be scanned')):
            agent.scan({'mode':'DEVOPS'},'sast')
        self.assertEqual(seen,[('sast','SKIPPED')])

    def test_devsecops_sast_runs_approved_rules(self):
        stages=[];calls=[]
        with patch.object(agent,'stage',side_effect=lambda *args:stages.append(args)),\
             patch.object(agent,'source',return_value=Path('/tmp/project')),\
             patch.object(agent.shutil,'which',return_value='/usr/local/bin/semgrep'),\
             patch.object(agent,'execute',side_effect=lambda args:calls.append(args)),\
             patch.object(agent.Path,'is_file',return_value=True),\
             patch.object(agent.os,'access',return_value=True):
            agent.scan({'mode':'DEVSECOPS'},'sast')
        self.assertEqual(stages,[('sast','RUNNING'),('sast','SUCCESS')])
        self.assertEqual(calls[0][:2],['semgrep','scan'])
        self.assertIn('--error',calls[0])
        self.assertIn('client-semgrep-rules.yml',calls[0][-3])

    def test_devsecops_trivy_scans_vulnerabilities_and_secrets(self):
        stages=[];calls=[]
        def execute(args, **kwargs):
            calls.append(args)
            Path(args[args.index('--output')+1]).write_text('{"SchemaVersion":2,"Results":[]}')
        with patch.object(agent,'stage',side_effect=lambda *args:stages.append(args)),\
             patch.object(agent,'source',return_value=Path('/tmp/project')),\
             patch.object(agent.shutil,'which',return_value='/usr/bin/trivy'),\
             patch.object(agent,'execute',side_effect=execute):
            agent.scan({'mode':'DEVSECOPS','build':{}},'sca')
        self.assertEqual(stages,[('sca','RUNNING'),('sca','SUCCESS')])
        self.assertIn('vuln,secret',calls[0]);self.assertIn('--exit-code',calls[0]);self.assertIn('--include-dev-deps',calls[0]); self.assertIn('json',calls[0])

    def test_scanner_absent_fails_not_skipped_or_succeeded(self):
        stages=[]
        with patch.object(agent,'stage',side_effect=lambda *args:stages.append(args)),\
             patch.object(agent,'source',return_value=Path('/tmp/project')),\
             patch.object(agent.shutil,'which',return_value=None),redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit):agent.scan({'mode':'DEVSECOPS'},'sast')
        self.assertEqual(stages,[('sast','RUNNING'),('sast','FAILED')])

    def test_pipeline_uses_trusted_helper_in_real_stages(self):
        text=(ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        for item in ("stage('SAST Semgrep')","stage('SCA Trivy')",'autokom_agent_local.py sast','autokom_agent_local.py sca'):
            self.assertIn(item,text)
        self.assertIn('skipDefaultCheckout(true)',text)

    def test_approved_rules_provisioned_without_secrets(self):
        ansible=(ROOT/'ansible/roles/platform_runtime/tasks/main.yml').read_text()
        self.assertIn('/usr/local/share/autokom-security/client-semgrep-rules.yml',ansible)
        self.assertNotIn('/etc/autokom/secrets/',str(ROOT/'scripts/runtime/autokom-client-semgrep-rules.yml'))

    def test_broker_returns_only_public_metadata(self):
        with patch('autokom_platform_broker._cloudflare_settings',return_value={
            'suffix':'auto.ikom.mg','zone_name':'ikom.mg','address':'56.228.86.160',
            'token':'VERY-SENSITIVE-TOKEN','zone':'secret-zone'}):
            result=_domain_config_check({})
        self.assertEqual(result['ingress_ipv4'],'56.228.86.160')
        self.assertEqual(result['managed_suffix'],'auto.ikom.mg')
        self.assertNotIn('token',result)
        self.assertNotIn('zone',result)
