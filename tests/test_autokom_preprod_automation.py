import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_dynamic_checks as dynamic
import autokom_preprod_admin as admin
import autokom_deepsast_admin as model
import autokom_jenkins_credentials as credentials

spec = importlib.util.spec_from_file_location('autokom_stage_config', ROOT / 'scripts/preprod/configure.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class DynamicChecks(TestCase):
    def test_only_candidate_loopback_and_headers(self):
        class Response:
            status = 200
            headers = {'Content-Type':'text/html; charset=utf-8'}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def get(request, timeout):
            self.assertEqual(request.full_url,'http://127.0.0.1:18123/health')
            self.assertEqual(request.method,'GET')
            self.assertEqual(timeout,4)
            return Response()

        self.assertEqual(dynamic.inspect_candidate(18123,'/health',open_request=get),
            {'http':200,'warnings':['CSP_MISSING','NOSNIFF_MISSING','FRAME_POLICY_MISSING']})

    def test_never_accept_custom_target(self):
        for path in ('http://external.example/', '//other', '/a?b=3', '/a%0A'):
            with self.assertRaises(ValueError):
                dynamic.inspect_candidate(18123, path, open_request=lambda *_: self.fail('Unexpected request'))

    def test_no_redirect_follow(self):
        calls = []
        def get(req, timeout):
            calls.append(req.full_url)
            raise HTTPError(req.full_url, 302, 'Found', {'Location':'https://outside.example/'}, io.BytesIO())
        self.assertEqual(dynamic.inspect_candidate(18123,'/',open_request=get)['http'],302)
        self.assertEqual(len(calls),1)


class PreprodRecovery(TestCase):
    def test_copied_env_repaired_without_using_production_db(self):
        with tempfile.TemporaryDirectory() as td:
            etc = Path(td)
            prod = {'DB_NAME':'production', 'DB_USER':'production', 'DB_PASSWORD':'not-staging'}
            backend = etc/'backend.env'
            backend.write_text('copied')
            def values(path):
                if path.name == 'runtime.env':
                    return {'AUTOKOM_INGRESS_IPV4':'8.8.8.8'}
                if path.name == 'cloudflare-dns.env':
                    return {}
                return prod
            with patch.object(stage,'ETC',etc), patch.object(stage,'private',side_effect=values), \
                 patch.object(stage,'provision_database') as provision, patch.object(stage,'check_stage_database') as probe:
                stage.init()
            text = backend.read_text()
            self.assertIn('DB_NAME="autokom_preprod"',text)
            self.assertNotIn('not-staging',text)
            self.assertEqual(list(etc.glob('backend.env.production-copy-*'))[0].read_text(),'copied')
            self.assertTrue(provision.call_args.kwargs['reset'])
            self.assertEqual(provision.call_args.args,probe.call_args.args)

    def test_no_migration_through_unknown_db(self):
        with tempfile.TemporaryDirectory() as td:
            etc = Path(td)
            (etc/'backend.env').write_text('another')
            def values(path):
                if path.name == 'runtime.env':
                    return {'AUTOKOM_INGRESS_IPV4':'8.8.8.8'}
                if path.parent == etc:
                    return {'DB_NAME':'other'}
                return {'DB_NAME':'production'}
            with patch.object(stage,'ETC',etc), patch.object(stage,'private',side_effect=values), \
                 patch.object(stage,'provision_database') as provision:
                with self.assertRaises(RuntimeError):
                    stage.init()
                provision.assert_not_called()

    def test_wrapper_rejects_foreign_file(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td)/'autokom-pre'
            exe.write_text('other')
            with patch.object(admin,'COMMAND',exe), self.assertRaises(RuntimeError):
                admin.install_wrapper()
            self.assertEqual(exe.read_text(),'other')

    def test_jenkins_credential_id_is_scoped(self):
        env = {'AUTOKOM_RUNNER_SHARED_KEY':'Z'*48}
        with self.assertRaises(credentials.CredentialSetupError):
            credentials.provision(env,lambda *a: self.fail('Unexpected request'),credential_id='foreign')
        self.assertIn(b'autokom-preprod-runner-key',credentials.credential_xml(env,'autokom-preprod-runner-key'))

    def test_shared_model_requires_verified_blob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in model.NAMES:
                (root/name).write_text('not a valid blob')
            self.assertFalse(model.check_model(root))
