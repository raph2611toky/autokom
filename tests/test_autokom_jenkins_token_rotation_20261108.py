"""Regression tests for secure Jenkins API token rotation."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, call
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/lib'))
import autokom_jenkins_token as jt
import autokom_runtime_admin as runtime

EXISTING = {
    'AUTOKOM_JENKINS_URL':'http://127.0.0.1:8080',
    'AUTOKOM_JENKINS_USER':'AdminJenkins',
    'AUTOKOM_JENKINS_API_TOKEN':'old-'+'0'*32,
    'AUTOKOM_JENKINS_JOB':'autokom-client-deploy',
    'AUTOKOM_RUNNER_SHARED_KEY':'r'*64,
}
NEW_TOKEN = 'fresh-'+'1'*32


class JenkinsTokenTests(unittest.TestCase):
    def configured(self):
        return patch.object(jt, '_configured', return_value=EXISTING.copy())

    def test_check_401_reports_expired_token_and_no_write(self):
        with self.configured(), patch.object(jt,'jenkins_http',return_value=(401,b'')), \
             patch.object(jt,'write_env',side_effect=AssertionError('read only')):
            self.assertEqual(jt.check(), 1)

    def test_check_ok_never_asks_for_token(self):
        with self.configured(), patch.object(jt,'jenkins_http',side_effect=[
             (200,b'{"authenticated":true,"name":"AdminJenkins"}'),(200,b'{}')]), \
             patch.object(jt, '_broker_ready',return_value=True), \
             patch.object(jt,'_broker_identity',return_value=True), \
             patch.object(jt,'_prompt_new_token',side_effect=AssertionError('never prompt')):
            self.assertEqual(jt.check(), 0)

    def test_invalid_token_is_rejected_before_backup_write_and_restart(self):
        with self.configured(), patch.object(jt,'_prompt_new_token',return_value=NEW_TOKEN), \
             patch.object(jt,'jenkins_http',return_value=(401,b'')), \
             patch.object(jt,'backup',side_effect=AssertionError('never backup')), \
             patch.object(jt,'write_env',side_effect=AssertionError('never write')), \
             patch.object(jt,'_systemctl',side_effect=AssertionError('never restart')):
            with self.assertRaisesRegex(jt.JenkinsTokenError, '401'):
                jt.apply()

    def test_unauthenticated_200_is_rejected(self):
        with patch.object(jt,'jenkins_http',return_value=(200,b'{"authenticated":false}')):
            with self.assertRaisesRegex(jt.JenkinsTokenError,'anonyme'):
                jt._identity(EXISTING)

    def test_job_access_requires_permission(self):
        with patch.object(jt,'jenkins_http',return_value=(403,b'')):
            with self.assertRaisesRegex(jt.JenkinsTokenError,'job refusé'):
                jt._job_access(EXISTING)

    def test_valid_rotation_updates_only_token_and_reloads_broker(self):
        values=[]
        with self.configured(), patch.object(jt,'_prompt_new_token',return_value=NEW_TOKEN), \
             patch.object(jt,'jenkins_http',side_effect=[
                  (200,b'{"authenticated":true,"name":"AdminJenkins"}'), (200,b'{}')]), \
             patch.object(jt,'_broker_ready',return_value=True), \
             patch.object(jt,'_broker_identity',return_value=True), \
             patch.object(jt,'backup',return_value=Path('/secure/archived')), \
             patch.object(jt,'write_env',side_effect=lambda p,env: values.append(env.copy())), \
             patch.object(jt,'_systemctl') as service, \
             patch.object(jt.os,'geteuid',return_value=0):
            self.assertEqual(jt.apply(),0)
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['AUTOKOM_JENKINS_API_TOKEN'],NEW_TOKEN)
        self.assertEqual({k:v for k,v in values[0].items() if k != 'AUTOKOM_JENKINS_API_TOKEN'},
                         {k:v for k,v in EXISTING.items() if k != 'AUTOKOM_JENKINS_API_TOKEN'})
        service.assert_called_once_with('restart', jt.BROKER_UNIT)

    def test_rollback_when_broker_fails_to_restart(self):
        values=[]
        with self.configured(), patch.object(jt,'_prompt_new_token',return_value=NEW_TOKEN), \
             patch.object(jt,'jenkins_http',side_effect=[
                  (200,b'{"authenticated":true,"name":"AdminJenkins"}'), (200,b'{}')]), \
             patch.object(jt,'_broker_ready',side_effect=[True,False,True]), \
             patch.object(jt,'_broker_identity',return_value=True), \
             patch.object(jt,'backup',return_value=Path('/secure/archived')), \
             patch.object(jt,'write_env',side_effect=lambda p,env: values.append(env.copy())), \
             patch.object(jt,'_systemctl') as service, \
             patch.object(jt.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(jt.JenkinsTokenError,'restaurée'):
                jt.apply()
        self.assertEqual([x['AUTOKOM_JENKINS_API_TOKEN'] for x in values], [NEW_TOKEN, EXISTING['AUTOKOM_JENKINS_API_TOKEN']])
        self.assertEqual(service.call_count, 2)

    def test_valid_but_unchanged_token_refreshes_old_process(self):
        with self.configured(), patch.object(jt,'_prompt_new_token',return_value=EXISTING['AUTOKOM_JENKINS_API_TOKEN']), \
             patch.object(jt,'jenkins_http',side_effect=[
                  (200,b'{"authenticated":true}'),(200,b'{}')]), \
             patch.object(jt,'_broker_ready',return_value=True), \
             patch.object(jt,'_broker_identity',return_value=True), \
             patch.object(jt,'backup',side_effect=AssertionError('no save')), \
             patch.object(jt,'write_env',side_effect=AssertionError('no write')), \
             patch.object(jt,'_systemctl') as service, \
             patch.object(jt.os,'geteuid',return_value=0):
            self.assertEqual(jt.apply(),0)
            service.assert_called_once_with('restart',jt.BROKER_UNIT)

    def test_broker_unconfirmed_after_update_rolls_back(self):
        values=[]
        with self.configured(), patch.object(jt,'_prompt_new_token',return_value=NEW_TOKEN), \
             patch.object(jt,'jenkins_http',side_effect=[
                  (200,b'{"authenticated":true}'),(200,b'{}')]), \
             patch.object(jt,'_broker_ready',return_value=True), \
             patch.object(jt,'_broker_identity',side_effect=[False,True]), \
             patch.object(jt,'backup',return_value=Path('/secure/archived')), \
             patch.object(jt,'write_env',side_effect=lambda p,env: values.append(env.copy())), \
             patch.object(jt,'_systemctl'), \
             patch.object(jt.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(jt.JenkinsTokenError,'restaurée'):
                jt.apply()
        self.assertEqual([v['AUTOKOM_JENKINS_API_TOKEN'] for v in values],
                         [NEW_TOKEN, EXISTING['AUTOKOM_JENKINS_API_TOKEN']])

    def test_broker_probe_never_receives_token(self):
        compile(jt._BROKER_IDENTITY_PROBE, '<autokom-broker-probe>', 'exec')
        with patch.object(jt.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(jt._broker_identity())
        self.assertEqual(run.call_args.args[0][0:4], ['runuser','-u','autokom','--'])
        self.assertNotIn(NEW_TOKEN,repr(run.call_args))

    def test_no_token_in_argument_or_systemd_command(self):
        with patch.object(jt.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            jt._systemctl('restart',jt.BROKER_UNIT)
        self.assertEqual(run.call_args.args[0],['systemctl','restart',jt.BROKER_UNIT])
        self.assertNotIn(NEW_TOKEN,repr(run.call_args))

    def test_token_prompt_masked_and_confirmed(self):
        with patch.object(jt.os,'isatty',return_value=True), \
             patch.object(jt.getpass,'getpass',side_effect=[NEW_TOKEN,NEW_TOKEN]) as prompt:
            self.assertEqual(jt._prompt_new_token(), NEW_TOKEN)
            self.assertEqual(prompt.call_count,2)

    def test_prompt_does_not_run_without_tty(self):
        with patch.object(jt.os,'isatty',return_value=False), \
             patch.object(jt.getpass,'getpass',side_effect=AssertionError('no prompt')):
            with self.assertRaisesRegex(jt.JenkinsTokenError,'Terminal interactif'):
                jt._prompt_new_token()

    def test_cli_parses_subcommand_check_and_apply(self):
        with patch.object(runtime.os,'geteuid',return_value=0), \
             patch.object(jt,'main',return_value=0) as run:
            self.assertEqual(runtime.main(['jenkins-token','--check']),0)
            run.assert_called_once_with(apply_mode=False)
            run.reset_mock()
            self.assertEqual(runtime.main(['jenkins-token','--apply']),0)
            run.assert_called_once_with(apply_mode=True)

if __name__ == '__main__':
    unittest.main()
