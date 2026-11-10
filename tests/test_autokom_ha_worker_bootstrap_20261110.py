"""Worker preparation and metadata-only GCP credentials: purely local tests."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'terraform/modules/gcp_regional_ha/files'


def load(filename, alias):
    spec = importlib.util.spec_from_file_location(alias, SRC / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = load('worker-health.py', 'autokom_worker_health')
probe = load('worker-probe.py', 'autokom_worker_probe')
preflight_spec = importlib.util.spec_from_file_location('autokom_ha_preflight_for_worker', ROOT / 'scripts/ha/autokom_ha_preflight.py')
preflight = importlib.util.module_from_spec(preflight_spec)
preflight_spec.loader.exec_module(preflight)


class Reply(io.BytesIO):
    def __init__(self, obj, status=200):
        super().__init__(json.dumps(obj).encode('utf-8'))
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class TestAutokomHAWorker(unittest.TestCase):
    def test_worker_template_visible_but_not_client_ready(self):
        doc = {'properties': {'metadata': {'items': [{'key': 'startup-script', 'value': '# autokom-ha-worker-preparation-only'}]}}}
        self.assertEqual(preflight.bootstrap_mode(doc), 'worker')
        self.assertNotEqual(preflight.bootstrap_mode(doc), 'application_or_custom')

    def test_preflight_validates_names(self):
        self.assertEqual(probe.require_config({
            'project_id': 'plucky-stacker-480421-c4', 'region': 'us-central1', 'artifact_repository': 'autokom-ha-images'}),
            ('plucky-stacker-480421-c4', 'us-central1', 'autokom-ha-images'))
        for project, region, repo in [('bad/project', 'us-central1', 'images'),
                                     ('valid-project', 'us-central1; rm -rf /', 'images'),
                                     ('valid-project', 'us-central1', '../../other')]:
            with self.subTest(project=project, region=region, repo=repo):
                with self.assertRaises(ValueError):
                    probe.require_config({'project_id': project, 'region': region, 'artifact_repository': repo})

    def test_auth_probe_reads_metadata_and_repository_only(self):
        responses = [Reply({'access_token': 'a'*40}), Reply({'name': 'projects/demo/locations/us-central1/repositories/autokom-ha-images'})]
        urls = []
        def opener(req, timeout):
            urls.append(req.full_url)
            if len(urls) == 2:
                self.assertEqual(req.headers['Authorization'], 'Bearer ' + 'a'*40)
            return responses[len(urls)-1]
        command = Mock(return_value=subprocess.CompletedProcess([], 0, '27.3.1\n', ''))
        cfg = {'project_id': 'plucky-stacker-480421-c4', 'region': 'us-central1', 'artifact_repository': 'autokom-ha-images'}
        self.assertTrue(probe.probe(cfg, opener=opener, runner=command))
        self.assertEqual(len(urls), 2)
        self.assertIn('artifactregistry.googleapis.com', urls[1])
        self.assertNotIn('gcloud', ' '.join(command.call_args.args[0]))

    def test_docker_unready_blocks_registries(self):
        command = Mock(return_value=subprocess.CompletedProcess([], 1, '', ''))
        cfg = {'project_id': 'plucky-stacker-480421-c4', 'region': 'us-central1', 'artifact_repository': 'autokom-ha-images'}
        with self.assertRaisesRegex(RuntimeError, 'Docker'):
            probe.probe(cfg, opener=Mock(), runner=command)

    def test_health_requires_marker_and_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'marker'
            socket_file = Path(tmp) / 'sock'
            self.assertFalse(health.prepared(marker, socket_file))
            marker.touch()
            self.assertFalse(health.prepared(marker, socket_file))
            import socket
            sock = socket.socket(socket.AF_UNIX)
            try:
                sock.bind(str(socket_file))
                self.assertTrue(health.prepared(marker, socket_file))
            finally:
                sock.close()

    @patch.object(preflight, 'command')
    def test_worker_mig_still_refuses_customer_deployment(self, cmd):
        def reply(*args):
            action = ' '.join(args)
            if ' managed describe ' in ' ' + action + ' ':
                return {'versions': [{'instanceTemplate': '.../autokom-ha-tpl-worker'}]}
            if ' managed list-instances ' in ' ' + action + ' ':
                return [{'instance': '.../zones/us-central1-a/instances/autokom-ha-web-a', 'instanceStatus': 'RUNNING'},
                        {'instance': '.../zones/us-central1-f/instances/autokom-ha-web-b', 'instanceStatus': 'RUNNING'}]
            if ' backend-services describe ' in ' ' + action + ' ':
                return {'portName': 'http'}
            if ' backend-services get-health ' in ' ' + action + ' ':
                return {'healthStatus': [{'healthState': 'HEALTHY'}, {'healthState': 'HEALTHY'}]}
            if ' url-maps describe ' in ' ' + action + ' ':
                return {'defaultService': '...'}
            if ' forwarding-rules describe ' in ' ' + action + ' ':
                return {'IPAddress': '35.186.239.102'}
            if ' instance-templates describe ' in ' ' + action + ' ':
                return {'properties': {'metadata': {'items': [{'key': 'startup-script', 'value': 'autokom-ha-worker-preparation-only'}]}}}
            raise AssertionError(action)
        cmd.side_effect = reply
        res = preflight.inventory('plucky-stacker-480421-c4', 'us-central1', 'autokom-ha-rmig',
                                  'autokom-ha-backend', 'autokom-ha-map', 'autokom-ha-http', False)
        self.assertEqual(res['bootstrap_mode'], 'worker')
        self.assertFalse(res['can_host_customer_applications'])
        self.assertTrue(any('publication' in item.lower() for item in res['issues']))
        self.assertIn('autokom_ha_readiness_ok 0', preflight.to_prometheus(res))

    def test_health_http_endpoint_is_not_customer_application(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(('127.0.0.1', 0), health.handler_factory('/healthz', check=lambda: False))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/healthz', timeout=2)
            self.assertEqual(exc.exception.code, 503)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
        server = ThreadingHTTPServer(('127.0.0.1', 0), health.handler_factory('/healthz', check=lambda: True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/healthz', timeout=2) as response:
                self.assertEqual(response.status, 200)
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=2) as response:
                self.assertIn(b'no customer app deployed', response.read())
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_worker_startup_has_no_remote_installer(self):
        template = (SRC / 'worker-startup.sh.tftpl').read_text()
        self.assertIn('autokom-ha-worker-preparation-only', template)
        self.assertNotIn('curl | bash', template)
        self.assertNotIn('docker run', template)
        self.assertNotIn('service_account_key', template)
        self.assertIn("<<'AUTOKOM_HEALTH'", template)
        rendered = template.replace('${health_source}', (SRC / 'worker-health.py').read_text())
        rendered = rendered.replace('${probe_source}', (SRC / 'worker-probe.py').read_text())
        rendered = rendered.replace('${worker_settings}', json.dumps({
            'project_id':'plucky-stacker-480421-c4', 'region':'us-central1',
            'artifact_repository':'autokom-ha-images', 'health_port':8080,'health_path':'/healthz'}))
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'startup.sh'; file.write_text(rendered)
            proc = subprocess.run(['bash','-n',str(file)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == '__main__':
    unittest.main()
