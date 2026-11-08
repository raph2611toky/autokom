"""Missing Declarative Pipeline must fail before Jenkins reports a successful job."""
from __future__ import annotations
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import autokom_jenkins_pipeline as j
import autokom_runtime_admin as admin
import autokom_recover as recover

ENV={'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
     'AUTOKOM_JENKINS_USER':'AdminJenkins',
     'AUTOKOM_JENKINS_API_TOKEN':'opaque-fake-token'}

def response(items, code=200):
    return code,json.dumps({'plugins':items}).encode()

def plugin(active=True,enabled=True,name='pipeline-model-definition'):
    return {'shortName':name,'active':active,'enabled':enabled,'version':'1'}

class PipelineTests(unittest.TestCase):
    def test_a_job_can_exist_without_declarative_plugin(self):
        state=j.inspect(ENV,request=lambda *a,**kw:response([plugin(name='workflow-cps')]))
        self.assertFalse(state.ready)
        self.assertEqual(state.missing,('pipeline-model-definition',))

    def test_active_plugin_must_be_verified_from_live_api(self):
        req=MagicMock(return_value=response([plugin()]))
        state=j.inspect(ENV,request=req)
        self.assertTrue(state.ready)
        self.assertIn('pluginManager/api/json',req.call_args.args[2])

    def test_disabled_plugin_never_automatically_reenabled(self):
        state=j.inspect(ENV,request=lambda *a,**kw:response([plugin(False,False)]))
        self.assertEqual(state.disabled,('pipeline-model-definition',))
        with patch.object(j,'inspect',return_value=state),patch.object(j,'_install',side_effect=AssertionError('no install')):
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(j.PipelineError,'désactivé'):
                j.reconcile(apply=True,env=ENV)

    def test_installed_but_not_active_is_not_success(self):
        state=j.inspect(ENV,request=lambda *a,**kw:response([plugin(False,True)]))
        self.assertEqual(state.pending,('pipeline-model-definition',))

    def test_401_rejected_not_inferred_from_disk(self):
        with self.assertRaisesRegex(j.PipelineError,'HTTP 401'):
            j.inspect(ENV,request=lambda *a,**kw:(401,b''))

    def test_malformed_list_rejected(self):
        with self.assertRaisesRegex(j.PipelineError,'invalide'):
            j.inspect(ENV,request=lambda *a,**kw:(200,b'not json'))

    def test_check_does_not_install_or_restart(self):
        state=j.PipelineState(('pipeline-model-definition',),(),())
        with patch.object(j,'inspect',return_value=state),\
             patch.object(j,'_install',side_effect=AssertionError('no mutations')),\
             patch.object(j,'_safe_restart',side_effect=AssertionError('no restart')):
            with redirect_stdout(io.StringIO()):self.assertFalse(j.reconcile(apply=False,env=ENV).ready)

    def test_healthy_never_installs_or_restarts(self):
        healthy=j.PipelineState((),(),())
        with patch.object(j,'inspect',return_value=healthy),\
             patch.object(j,'_install',side_effect=AssertionError('no install')),\
             patch.object(j,'_safe_restart',side_effect=AssertionError('no restart')):
            with redirect_stdout(io.StringIO()):self.assertTrue(j.reconcile(apply=True,env=ENV).ready)

    def test_missing_install_then_safe_restart_then_live_verify(self):
        missing=j.PipelineState(('pipeline-model-definition',),(),())
        healthy=j.PipelineState((),(),())
        order=[]
        with patch.object(j,'inspect',return_value=missing),\
             patch.object(j,'_install',side_effect=lambda names,env:order.append(('install',names))),\
             patch.object(j,'_safe_restart',side_effect=lambda env:order.append(('restart',None))),\
             patch.object(j,'_wait_loaded',return_value=healthy) as loaded,\
             patch.object(j.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()):self.assertTrue(j.reconcile(apply=True,env=ENV).ready)
        self.assertEqual(order,[('install',['pipeline-model-definition']),('restart',None)])
        loaded.assert_called_once()

    def test_pending_restart_does_not_reinstall(self):
        pending=j.PipelineState((),(),('pipeline-model-definition',))
        with patch.object(j,'inspect',return_value=pending),\
             patch.object(j,'_install',side_effect=AssertionError('must not reinstall')),\
             patch.object(j,'_safe_restart') as reboot,\
             patch.object(j,'_wait_loaded',return_value=j.PipelineState((),(),())),\
             patch.object(j.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()):j.reconcile(apply=True,env=ENV)
        reboot.assert_called_once()

    def test_install_failure_does_not_restart(self):
        pending=j.PipelineState(('pipeline-model-definition',),(),())
        with patch.object(j,'inspect',return_value=pending),\
             patch.object(j,'_install',side_effect=RuntimeError('INTERNAL TOKEN SECRET')),\
             patch.object(j,'_safe_restart',side_effect=AssertionError('unexpected restart')),\
             patch.object(j.os,'geteuid',return_value=0):
            with redirect_stdout(io.StringIO()),self.assertRaises(j.PipelineError) as err:
                j.reconcile(apply=True,env=ENV)
        self.assertNotIn('SECRET',str(err.exception))

    def test_cli_will_not_send_token_to_public_or_wrong_port(self):
        for address in ('https://jenkins.example.com','http://0.0.0.0:8080','http://127.0.0.1:9999'):
            with self.subTest(address=address):
                with patch('jenkins_postinstall.JENKINS_URL','http://127.0.0.1:8080/'):
                    with self.assertRaises(j.PipelineError):j._verified_local_cli({**ENV,'AUTOKOM_JENKINS_URL':address})

    def test_official_profile_already_has_declarative_requirement(self):
        self.assertIn('pipeline-model-definition',(ROOT/'config/jenkins/plugins-baseline.txt').read_text())

    def test_jenkinsfile_requires_declarative_syntax(self):
        source=(ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertIn('pipeline {',source)
        self.assertIn('autokom-isolated',source)

    def test_runtime_command_has_check_apply(self):
        out=io.StringIO()
        with redirect_stdout(out),self.assertRaises(SystemExit) as exc:
            admin.main(['pipeline','--help'])
        self.assertEqual(exc.exception.code,0)
        self.assertIn('--check',out.getvalue());self.assertIn('--apply',out.getvalue())

    def test_jenkins_sync_requires_plugin_before_job_change(self):
        with patch.object(admin,'load_env',return_value=ENV),\
             patch.object(j,'reconcile',return_value=j.PipelineState(('pipeline-model-definition',),(),())) as verify,\
             patch.object(admin,'jenkins_request',side_effect=AssertionError('should not create job')):
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(admin.SetupError,'ne peut pas encore exécuter'):
                admin.jenkins_sync(apply=False)
        verify.assert_called_once()

    def test_recover_refuses_false_positive_when_declarative_missing(self):
        # Behavior enforced in source, even if backend/agent status is already OK.
        code=(ROOT/'scripts/lib/autokom_recover.py').read_text()
        self.assertIn('pipeline_inspect()',code)
        self.assertIn('pipeline_reconcile(apply=True)',code)
        self.assertIn('Pipeline Jenkins=',code)

if __name__=='__main__':unittest.main()
