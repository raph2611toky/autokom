#!/usr/bin/env python3
"""Network-independent regression tests for Jenkins auth diagnosis and Git UX."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/lib'))
import jenkins_postinstall as j
import autokom_cli as cli


class FakeResponse:
    status=200
    def __init__(self, content): self.content=content
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, max_size): return self.content[:max_size]


class JenkinsAuthTests(unittest.TestCase):
    def test_cli_auth_transport_is_http_with_private_file(self):
        with mock.patch.object(j,'command',return_value='ok') as cmd:
            self.assertEqual(j.cli('/run/test-auth', ['who-am-i']),'ok')
        args=cmd.call_args.args[0]
        self.assertIn('-http',args)
        self.assertNotIn('-webSocket',args)
        self.assertIn('@/run/test-auth',args)
        self.assertNotIn('SECRET',repr(args))

    def test_rest_proves_valid_credentials_over_localhost_only(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'token'; f.write_text('AdminJenkins:secret-token\n')
            payload=json.dumps({'authenticated':True,'name':'AdminJenkins'}).encode()
            owner = self
            def fake_build(*handlers):
                owner.assertTrue(any(isinstance(h,j.urllib.request.ProxyHandler) for h in handlers))
                class Opener:
                    def open(self, req, timeout=0):
                        self_req=req
                        owner.assertEqual(self_req.full_url, 'http://127.0.0.1:8080/whoAmI/api/json')
                        owner.assertEqual(self_req.get_header('Authorization'),
                                         'Basic QWRtaW5KZW5raW5zOnNlY3JldC10b2tlbg==')
                        return FakeResponse(payload)
                return Opener()
            with mock.patch.object(j.urllib.request,'build_opener',side_effect=fake_build):
                self.assertEqual(j.verify_rest_identity(f,'AdminJenkins'),'AdminJenkins')

    def test_rest_rejects_bad_token_and_never_prints_secret(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'token'; f.write_text('admin:VERY-PRIVATE-SECRET\n')
            class Opener:
                def open(self,*args,**kwargs):
                    raise HTTPError('http://127.0.0.1',401,'invalid',None,None)
            with mock.patch.object(j.urllib.request,'build_opener',return_value=Opener()):
                with self.assertRaises(j.AuthenticationRejected) as exc:
                    j.verify_rest_identity(f,'admin')
                self.assertNotIn('VERY-PRIVATE-SECRET',str(exc.exception))

    def test_rest_refuses_redirect_even_when_token_correct(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'token'; f.write_text('admin:secret\n')
            class Opener:
                def open(self,*args,**kwargs):
                    raise HTTPError('http://127.0.0.1',302,'Moved',{'Location':'https://external.invalid'},None)
            with mock.patch.object(j.urllib.request,'build_opener',return_value=Opener()):
                with self.assertRaisesRegex(RuntimeError,'redirige'):
                    j.verify_rest_identity(f,'admin')

    def test_valid_token_cli_failure_is_not_a_retriable_login_error(self):
        args=j.parse(['--apply','--interactive'])
        with mock.patch.object(j,'ensure_cli_jar'), \
             mock.patch.object(j,'ask',return_value='AdminJenkins') as ask, \
             mock.patch.object(j,'read_masked',return_value='VERY-PRIVATE-TOKEN') as masked, \
             mock.patch.object(j,'cli',side_effect=RuntimeError('failed -webSocket')), \
             mock.patch.object(j,'verify_rest_identity',return_value='AdminJenkins') as rest, \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaisesRegex(RuntimeError,'Token Jenkins accepté') as err:
                with j.authorization_context(args,'https://jenkins.example/',apply=True): pass
        ask.assert_called_once(); masked.assert_called_once(); rest.assert_called_once()
        self.assertNotIn('VERY-PRIVATE-TOKEN',str(err.exception)+stdout.getvalue())

    def test_bad_token_cli_failure_is_retriable(self):
        args=j.parse(['--apply','--interactive'])
        with mock.patch.object(j,'ensure_cli_jar'), \
             mock.patch.object(j,'ask',return_value='admin'), \
             mock.patch.object(j,'read_masked',return_value='BAD'), \
             mock.patch.object(j,'cli',side_effect=RuntimeError('generic cli problem')), \
             mock.patch.object(j,'verify_rest_identity',side_effect=j.AuthenticationRejected('HTTP401')):
            with self.assertRaisesRegex(RuntimeError,'après 3 essais'):
                with j.authorization_context(args,'https://jenkins.example/',apply=True): pass


class GitUXTests(unittest.TestCase):
    def run_update(self, old, new, *, color=True, exitcode=0):
        recorded=[]
        def command(args,**kwargs):
            recorded.append(('command',args))
            if args[-2:] == ['status','--porcelain']: return subprocess.CompletedProcess(args,0,stdout='')
            if args[-2:] == ['rev-parse','HEAD']:
                return subprocess.CompletedProcess(args,0,stdout=(old if sum(1 for x in recorded if x[1][-2:]==['rev-parse','HEAD'])==1 else new)+'\n')
            if 'diff' in args: return subprocess.CompletedProcess(args,0,stdout=' file.py | 2 +\u001b[32m+\u001b[0m\n')
            return subprocess.CompletedProcess(args,0,stdout='')
        def run(args,**kwargs):
            recorded.append(('run',args))
            return subprocess.CompletedProcess(args,exitcode,stdout='Updating ...\nFast-forward\n',stderr='From github\n')
        output_buffer = io.StringIO()
        output_buffer.isatty = lambda: color
        with mock.patch.object(cli,'command',side_effect=command), \
             mock.patch.object(cli.subprocess,'run',side_effect=run), \
             mock.patch.object(cli.os,'geteuid',return_value=0), \
             mock.patch.object(cli,'Activity'), \
             mock.patch.object(Path,'exists',return_value=True), \
             mock.patch.dict('os.environ',{'TERM':'xterm'}, clear=False), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(output_buffer):
            result=cli.main(['update'])
        return result,recorded,output_buffer.getvalue()

    def test_diffstat_uses_git_native_colors_for_new_revision(self):
        code,ops,out=self.run_update('1'*40,'2'*40)
        self.assertEqual(code,0)
        diff=[a for kind,a in ops if kind=='command' and 'diff' in a]
        self.assertEqual(len(diff),1)
        self.assertIn('--color=always',diff[0]); self.assertIn('--stat',diff[0])
        self.assertIn('--no-stat',next(a for kind,a in ops if kind=='run'))
        self.assertIn('file.py',out)

    def test_diffstat_no_ansi_outside_tty(self):
        _,ops,_=self.run_update('1'*40,'2'*40,color=False)
        diff=[a for kind,a in ops if kind=='command' and 'diff' in a]
        self.assertIn('--color=never',diff[0])

    def test_no_diffstat_when_same_revision(self):
        _,ops,_=self.run_update('3'*40,'3'*40)
        self.assertFalse(any('diff' in a for _,a in ops))

    def test_failure_does_not_show_misleading_diffstat(self):
        with self.assertRaises(cli.CLIError):
            self.run_update('1'*40,'2'*40,exitcode=128)


if __name__=='__main__': unittest.main()
