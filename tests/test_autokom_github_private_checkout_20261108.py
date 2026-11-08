"""Offline regression tests: GitHub private sources, no global Jenkins token."""
from __future__ import annotations

import ast
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AGENT_FILE = ROOT / 'scripts/runtime/autokom_agent_local.py'
GITHUB_SERVICE = ROOT / 'platform/backend/apps/deployments/runs/github_sources.py'

spec = importlib.util.spec_from_file_location('autokom_agent_private', AGENT_FILE)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

RUN = 'b319d4e6-aea5-414d-a865-e93fc14e7b93'
SHA = 'd' * 40
DOC = {'run_id': RUN, 'commit_sha': SHA, 'build': {'root': ''}}


def make_archive(files=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('repo-pinned/', '')
        for name, content in (files or {'src/main.jsx':'export default 1', 'package.json':'{}'}).items():
            z.writestr('repo-pinned/' + name, content)
    return buffer.getvalue()


class PrivateSourceAgentTests(unittest.TestCase):
    def test_valid_commit_snapshot_is_usable_without_git_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent._unpack_private_source(make_archive(), root, DOC)
            self.assertEqual((root/'src/main.jsx').read_text(), 'export default 1')
            self.assertFalse((root/'.git').exists())
            with patch.dict(os.environ, {'WORKSPACE':str(root)}):
                self.assertEqual(agent.source(DOC), root)
                with self.assertRaises(SystemExit):
                    agent.source({**DOC, 'commit_sha': 'e'*40})
                with self.assertRaises(SystemExit):
                    agent.source({**DOC, 'run_id':'a'*36})

    def test_rejects_zip_path_traversal_before_extracting_any_file(self):
        for evil in ('../../etc/passwd', '../outside', '/root/private', 'folder/../passwd',
                     'folder\\passwd', 'sub//entry'):
            with self.subTest(evil=evil), tempfile.TemporaryDirectory() as td:
                payload=make_archive({'safe.txt':'ok', evil:'bad'})
                with self.assertRaises(SystemExit):
                    agent._unpack_private_source(payload, Path(td), DOC)
                self.assertEqual(list(Path(td).iterdir()), [])

    def test_rejects_symlink_and_false_marker(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            info=zipfile.ZipInfo('repo-pinned/sneaky')
            info.create_system=3
            info.external_attr=(stat.S_IFLNK | 0o777)<<16
            archive.writestr(info, '../../etc/passwd')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                agent._unpack_private_source(stream.getvalue(), Path(td), DOC)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                agent._unpack_private_source(make_archive({'.autokom-source.json':'{}'}), Path(td), DOC)

    def test_refuses_oversized_archive_without_extracting(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(agent, 'SOURCE_EXPANDED_LIMIT', 5):
                with self.assertRaises(SystemExit):
                    agent._unpack_private_source(make_archive({'large.txt': 'abcdef'}), Path(td), DOC)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_private_fetch_uses_hmac_and_never_sends_github_credentials(self):
        class FakeResponse:
            status=200
            headers=types.SimpleNamespace(get_content_type=lambda: 'application/zip')
            def __init__(self, data): self.buffer=io.BytesIO(data)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return self.buffer.read(count)
        observed=[]
        class FakeOpener:
            def open(self, req, timeout=None):
                observed.append(req)
                return FakeResponse(make_archive())
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'WORKSPACE':td,'AUTOKOM_RUNNER_SHARED_KEY':'k'*48,
                     'AUTOKOM_API_BASE':'https://backend.auto.ikom.mg'}), \
                 patch.object(agent, 'build_opener', return_value=FakeOpener()):
                agent.private_source(DOC)
            self.assertEqual(len(observed),1)
            self.assertTrue(observed[0].full_url.endswith('/source/'))
            self.assertNotIn('Authorization', dict(observed[0].header_items()))
            self.assertTrue(observed[0].get_header('X-autokom-runner-signature'))
            self.assertEqual((Path(td)/'package.json').read_text(),'{}')

    def test_public_git_path_remains_and_global_credential_is_removed(self):
        groovy=(ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertIn('if (publicRepo)', groovy)
        self.assertIn("userRemoteConfigs:[[url:repositoryUrl]]", groovy)
        self.assertIn('private-source', groovy)
        self.assertIn('deleteDir()', groovy)
        self.assertNotIn('autokom-github-app', groovy)
        self.assertIn("credentialsId:'autokom-runner-key'", groovy)


class BackendSourceTests(unittest.TestCase):
    def setUp(self):
        # No Django or production database needed. Load the actual function AST
        # with stubbed authorization and GitHub network dependencies.
        tree=ast.parse(GITHUB_SERVICE.read_text(encoding='utf-8'))
        selected=[node for node in tree.body if (isinstance(node,ast.FunctionDef)
                  and node.name in ('authorized_source','_github_archive_url','download_source_archive'))
                  or isinstance(node,ast.ClassDef) and node.name=='SourceUnavailable']
        class APIException(Exception): pass
        class PermissionDenied(Exception): pass
        self.APIException=APIException
        self.PermissionDenied=PermissionDenied
        self.repo=types.SimpleNamespace(id=1450286432, full_name='raph2611toky/nexttest')
        self.actor=object()
        self.connection=types.SimpleNamespace(is_active=True,provider='GITHUB',organization_id=9,
                  installation_id=178993294,created_by=object())
        self.project=types.SimpleNamespace(organization_id=9,git_connection=self.connection,
                  repository_name=self.repo.full_name,repository_external_id=str(self.repo.id))
        self.run=types.SimpleNamespace(environment=types.SimpleNamespace(project=self.project),
                 commit_sha=SHA,requested_by=self.actor)
        class Query:
            def select_related(_self,*args):return _self
            def filter(_self,**kwargs):return _self
            def first(_self):return self.run
        RequestModel=types.SimpleNamespace(objects=Query(),Status=types.SimpleNamespace(RUNNING='RUNNING'))
        self.access=[]
        def get_repo(actor,installation_id,repo_id):
            self.access.append((actor,installation_id,repo_id))
            return self.connection,{'id':self.repo.id,'full_name':self.repo.full_name}
        ns={
          'APIException':APIException,'PermissionDenied':PermissionDenied,
          'DeploymentRun':RequestModel,'get_repository':get_repo,
          'get_user_token':lambda actor:'TOKEN_MUST_STAY_ON_DJANGO',
          're':re, 'requests':__import__('requests'),
          'tempfile':tempfile,'quote':__import__('urllib.parse',fromlist=['quote']).quote,
          'urlsplit':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit,
          'SHA':re.compile(r'^[a-f0-9]{40}$'),
          'REPO':re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'),
          'MAX_ARCHIVE_BYTES':128*1024*1024}
        self.ns=ns
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),
                     str(GITHUB_SERVICE),'exec'),ns)

    def test_authorization_binds_user_installation_repository_sha(self):
        repository, sha, token=self.ns['authorized_source'](RUN)
        self.assertEqual((repository,sha,token),('raph2611toky/nexttest',SHA,'TOKEN_MUST_STAY_ON_DJANGO'))
        self.assertEqual(self.access,[(self.actor,178993294,'1450286432')])
        self.connection.organization_id=100
        with self.assertRaises(self.PermissionDenied):self.ns['authorized_source'](RUN)

    def test_wrong_repo_identity_denied_even_when_github_token_exists(self):
        self.project.repository_external_id='55555'
        self.access.clear()
        with self.assertRaises(self.PermissionDenied):self.ns['authorized_source'](RUN)

    def test_internal_archive_route_requires_hmac_and_is_not_in_public_schema(self):
        urls=(ROOT/'platform/backend/config/urls.py').read_text(encoding='utf-8')
        views=(ROOT/'platform/backend/apps/deployments/runs/views.py').read_text(encoding='utf-8')
        self.assertIn("path('api/internal/runner/deployments/<uuid:run_id>/source/'", urls)
        self.assertIn('class RunnerSourceArchiveAPIView(APIView):', views)
        method=views.split('class RunnerSourceArchiveAPIView(APIView):',1)[1].split('class RuntimeLogsAPIView',1)[0]
        self.assertLess(method.index('check_runner_signature('),method.index('download_source_archive(run_id)'))
        self.assertIn("'Cache-Control'", method)
        self.assertIn("'X-Content-Type-Options'", method)
        self.assertNotIn("internal/runner",urls.split('client_api_patterns = [',1)[1].split('schema_view =',1)[0])

    def test_runtime_doctor_refuses_old_jenkinsfile(self):
        runtime=(ROOT/'scripts/lib/autokom_runtime_admin.py').read_text(encoding='utf-8')
        self.assertIn("mismatch = _pipeline_mismatch(JOB_SCRIPT.read_text",runtime)
        self.assertIn('sudo autokom runtime jenkins --apply',runtime)

    def test_short_lived_archive_redirect_does_not_forward_oauth_token(self):
        class Response:
            def __init__(self,status,headers=None,data=b''):
                self.status_code=status;self.headers=headers or {};self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,chunk_size):yield self.data
        urls=[]
        def fake_request(url,**options):
            urls.append((url,options))
            if len(urls)==1:
                return Response(302,{'Location':'https://codeload.github.com/raph2611toky/nexttest/legacy.zip/'+SHA})
            return Response(200,data=make_archive())
        result=self.ns['download_source_archive'](RUN,request=fake_request)
        self.assertEqual(result.read(),make_archive());result.close()
        self.assertEqual(len(urls),2)
        self.assertIn('Authorization',urls[0][1]['headers'])
        self.assertNotIn('Authorization',urls[1][1]['headers'])
        self.assertTrue(urls[0][1]['allow_redirects'] is False)
        self.assertTrue(urls[1][1]['allow_redirects'] is False)
        self.assertNotIn('TOKEN_MUST_STAY_ON_DJANGO',urls[1][0])

    def test_external_redirect_and_large_download_denied(self):
        class Response:
            def __init__(self,status,headers=None,data=b''):
                self.status_code=status;self.headers=headers or {};self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,chunk_size):yield self.data
        def evil(url,**options):
            return Response(302,{'Location':'https://evil.invalid/steal'})
        with self.assertRaises(self.APIException):
            self.ns['download_source_archive'](RUN,request=evil)
        called=[]
        def large(url,**options):
            called.append(url)
            if len(called)==1:
                return Response(302,{'Location':'https://codeload.github.com/raph2611toky/nexttest/legacy.zip/'+SHA})
            return Response(200,data=b'x'*100)
        self.ns['MAX_ARCHIVE_BYTES']=30
        with self.assertRaises(self.APIException):
            self.ns['download_source_archive'](RUN,request=large)

if __name__=='__main__': unittest.main()
