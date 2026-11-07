"""All network/Jenkins operations mocked. Run: python3 -m unittest -v tests.test_autokom_jenkins_plugin_recovery_20261107"""
import io
import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plugin_recovery', ROOT / 'scripts/lib/jenkins_plugin_recovery.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Fixture:
    def __init__(self):
        self.events = []
    def event(self,*args):
        self.events.append(args)

class PluginRecoveryTests(unittest.TestCase):
    def test_one_success(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            def cli(auth, args, **kwargs):
                calls.append(args)
                return 'ok'
            rendered=[]
            installed = m.install_missing(['pipeline-rest-api', 'pipeline-stage-view'],cli,'/root/auth',output=lambda *x:rendered.append(x),log_dir=td)
            self.assertEqual(installed, ['pipeline-rest-api','pipeline-stage-view'])
            self.assertEqual(calls, [['install-plugin','pipeline-rest-api'],['install-plugin','pipeline-stage-view']])
            logs = list(Path(td).glob('plugins-*.jsonl'))
            self.assertEqual(len(logs),1)
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
            lines = logs[0].read_text()
            self.assertIn('pipeline-stage-view',lines)
            self.assertNotIn('/root/auth',lines)

    def test_transient_failure_refresh_and_success(self):
        with tempfile.TemporaryDirectory() as td:
            calls=[]
            def cli(auth,args,**kwargs):
                calls.append(list(args))
                if len(calls)==1: raise RuntimeError('HTTP 502')
                if args[:2]==['groovy','=']: return 'AUTOKOM_UPDATE_CENTER_REFRESHED'
                return 'ok'
            self.assertEqual(m.install_missing(['git'],cli,'/root/secret',output=lambda *x:None,log_dir=td),['git'])
            self.assertEqual([x[:2] for x in calls], [['install-plugin','git'],['groovy','='],['install-plugin','git']])
            self.assertIn('retry-install-plugin',next(Path(td).glob('plugins-*.jsonl')).read_text())

    def test_refresh_only_once_for_multiple_plugins(self):
        with tempfile.TemporaryDirectory() as td:
            invocations=[]
            def cli(auth,args,**kwargs):
                invocations.append(tuple(args))
                if args[:2]==['groovy','=']:
                    return 'AUTOKOM_UPDATE_CENTER_REFRESHED'
                if invocations.count(tuple(args))==1: raise RuntimeError('timeout')
                return 'ok'
            self.assertEqual(len(m.install_missing(['pipeline-rest-api','git'],cli,'/root/auth',output=lambda *x:None,log_dir=td)),2)
            self.assertEqual(sum(i[:2]==('groovy','=') for i in invocations),1)

    def test_failure_continues_and_reports_all(self):
        with tempfile.TemporaryDirectory() as td:
            calls=[]
            def cli(auth,args,**kwargs):
                calls.append(args)
                if args[:2]==['groovy','=']:
                    return 'AUTOKOM_UPDATE_CENTER_REFRESHED'
                if args[1] == 'bad': raise RuntimeError('Mirror 404 token=secret123')
                return 'ok'
            with self.assertRaisesRegex(RuntimeError,'bad.*1 téléchargé'):
                m.install_missing(['bad','git'],cli,'/private/secret',output=lambda *x:None,log_dir=td,verified_fallback=lambda *args:False)
            log = next(Path(td).glob('plugins-*.jsonl')).read_text()
            for secret in ('/private/secret','secret123'):
                self.assertNotIn(secret, log)
            self.assertTrue(any(call[:2]==['install-plugin','git'] for call in calls))

    def test_stage_fallback_after_two_failures(self):
        with tempfile.TemporaryDirectory() as td:
            events=[]
            def cli(auth,args,**kwargs):
                if args[:2]==['groovy','=']: return 'AUTOKOM_UPDATE_CENTER_REFRESHED'
                raise RuntimeError('network error')
            def fallback(cli_fn,auth,audit):
                events.append('fallback')
                return True
            self.assertEqual(m.install_missing(['pipeline-stage-view'],cli,'/auth',output=lambda *x:None,log_dir=td,verified_fallback=fallback),['pipeline-stage-view'])
            self.assertEqual(events,['fallback'])

    def test_invalid_plugin_and_no_log(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):m.install_missing(['-restart'],lambda *x:None,'/auth',output=lambda *x:None,log_dir=td)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_refresh_requires_explicit_marker(self):
        with self.assertRaisesRegex(RuntimeError,'ne confirme pas'):
            m.refresh_update_center(lambda *args,**kwargs:'', '/auth')

    def test_log_private_mode_check(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)/'log';directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            with m.PluginAudit(directory) as log:
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                log.event('git','preflight','ok')
            self.assertEqual(len(list(directory.iterdir())),1)

    def test_log_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'target';target.mkdir()
            symlink=Path(td)/'symlink';symlink.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError,'lien symbolique'):
                m.PluginAudit(symlink)

    def test_safe_reason_does_not_expose_details(self):
        secret='ghp_FAKE_SECRETS'
        self.assertEqual(m.safe_reason(RuntimeError('some token='+secret)), 'jenkins_install_error')
        self.assertEqual(m.safe_reason(RuntimeError('HTTP 404 token='+secret)), 'plugin_or_artifact_missing')

    def test_fallback_requires_sha256_and_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            archive=Path(td)/'plugin.hpi'
            payload=b'not a valid archive'*1000
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,n):
                    nonlocal payload
                    out,payload=payload[:n],payload[n:]
                    return out
                def geturl(self):return 'https://repo.jenkins-ci.org/test'
            with self.assertRaisesRegex(RuntimeError,'SHA256'):
                m._verified_download('https://repo.jenkins-ci.org/test',archive,opener=lambda *args,**kwargs:Response())

    def test_refuse_https_downgrade(self):
        with tempfile.TemporaryDirectory() as td:
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def geturl(self):return 'http://unexpected.example.com/plugin'
            with self.assertRaisesRegex(RuntimeError,'HTTPS'):
                m._verified_download('https://repo.jenkins-ci.org/test',Path(td)/'plugin.hpi',opener=lambda *args,**kwargs:Response())

    def test_fallback_download_verifies_actual_bytes_and_cli_file(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nShort-Name: pipeline-stage-view\nPlugin-Version: 2.41\n')
            z.writestr('payload.bin', bytes(range(256)) * 8)
        data=buffer.getvalue()
        class Response:
            def __init__(self):self.idx=0
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return 'https://repo.jenkins-ci.org/test'
            def read(self,n):
                out=data[self.idx:self.idx+n]
                self.idx+=len(out)
                return out
        calls=[]
        audit=Fixture()
        def fake_cli(auth,args,**kwargs):
            if args[:2]==['groovy','=']:
                return 'AUTOKOM_JENKINS_VERSION:2.580.2'
            self.assertEqual(args[:1],['install-plugin'])
            self.assertEqual(Path(args[1]).read_bytes(), data)
            calls.append(args)
            return 'ok'
        with mock.patch.object(m,'PINNED_STAGE_VIEW_SHA256', hashlib.sha256(data).hexdigest()):
            self.assertTrue(m.stage_view_verified_fallback(fake_cli,'/root/auth',audit,opener=lambda *x,**kw:Response()))
        self.assertEqual(len(calls),1)
        self.assertTrue(any(x[1]=='verified-archive-install' and x[2]=='ok' for x in audit.events))

    def test_fallback_refuses_old_core(self):
        audit=Fixture()
        self.assertFalse(m.stage_view_verified_fallback(lambda *a,**k:'AUTOKOM_JENKINS_VERSION:2.440.3','/auth',audit))
        self.assertEqual(audit.events[-1][2],'incompatible_or_unknown')

    def test_fallback_fails_closed_if_all_mirrors_bad(self):
        audit=Fixture()
        def bad(*args,**kwargs):raise OSError('offline')
        self.assertFalse(m.stage_view_verified_fallback(lambda *a,**k:'AUTOKOM_JENKINS_VERSION:2.580.2','/auth',audit,opener=bad))
        self.assertEqual(len(audit.events),3)


    def test_existing_corrupted_archive_is_not_silently_treated_as_installed(self):
        spec = importlib.util.spec_from_file_location('jenkins_postinstall_test',ROOT / 'scripts/lib/jenkins_postinstall.py')
        post = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(post)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'pipeline-stage-view.jpi').write_bytes(b'partial-download')
            with self.assertRaisesRegex(RuntimeError, 'Archive Jenkins invalide'):
                post.local_plugin_ids(Path(td))

    def test_existing_valid_archive_is_preserved(self):
        spec = importlib.util.spec_from_file_location('jenkins_postinstall_test2',ROOT / 'scripts/lib/jenkins_postinstall.py')
        post = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(post)
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / 'pipeline-stage-view.jpi'
            with zipfile.ZipFile(file,'w') as z:
                z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nPlugin-Version: 2.41\n')
            self.assertEqual(post.local_plugin_ids(Path(td)), {'pipeline-stage-view'})
            (Path(td) / 'pipeline-stage-view.jpi.disabled').touch()
            self.assertEqual(post.local_plugin_ids(Path(td)), set())

class IntegrationContractTests(unittest.TestCase):
    def test_main_calls_resumable_installer(self):
        text=(ROOT/'scripts/lib/jenkins_postinstall.py').read_text()
        self.assertIn('install_missing([p.split',text)
        self.assertIn("missing = ['matrix-auth']",text)
        self.assertNotIn("cli(auth_file, ['install-plugin', *missing]",text)

    def test_pipeline_rest_api_before_stage_view(self):
        plugins=(ROOT/'config/jenkins/plugins-baseline.txt').read_text().splitlines()
        self.assertLess(plugins.index('pipeline-rest-api'), plugins.index('pipeline-stage-view'))

if __name__=='__main__':unittest.main()
