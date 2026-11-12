"""Non-destructive regression tests for Docker internal bridge and gh account auth."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


worker = load(BASE / 'terraform/modules/gcp_regional_ha/files/worker-agent.py', 'ak_worker')
health = load(BASE / 'terraform/modules/gcp_regional_ha/files/worker-delivery-health.py', 'ak_health')
update = load(BASE / 'scripts/lib/autokom_private_update.py', 'ak_update')
ENV = '94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0'
RUN = 'a789ba5a-f920-4e90-a624-a6da3d6088ca'
APP = {'environment_id': ENV, 'run_id': RUN, 'image': 'fake:tag',
       'bind_port': 35309, 'internal_port': 8080,
       'healthcheck': '/', 'hosts': ['ladyportfolio.auto.ikom.mg'],
       'cpu_millicores': 750, 'memory_mb': 1024}


class DockerBridgeTests(TestCase):
    def test_docker_private_ip_exact_network(self):
        blob = json.dumps([{'NetworkSettings': {'Networks': {
            worker.network_name(APP): {'IPAddress': '172.18.0.2'},
            'some-other-network': {'IPAddress': '8.8.8.8'},
        }}}])
        with patch.object(worker, 'docker', return_value=SimpleNamespace(stdout=blob)):
            self.assertEqual(worker.container_ipv4(APP), '172.18.0.2')

    def test_missing_expected_network_rejected(self):
        blob = json.dumps([{'NetworkSettings': {'Networks': {
            'other-network': {'IPAddress': '172.19.0.3'}
        }}}])
        with patch.object(worker, 'docker', return_value=SimpleNamespace(stdout=blob)):
            with self.assertRaises(worker.WorkerError):
                worker.container_ipv4(APP)

    def test_health_probes_container_not_localhost(self):
        called = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Opener:
            def open(self, request, timeout):
                called.append(request.full_url)
                called.append(request.get_header('Host'))
                return Response()
        with patch.object(worker, 'container_ipv4', return_value='172.18.0.2'), \
             patch.object(worker.urllib.request, 'build_opener', return_value=Opener()):
            self.assertTrue(worker.health(APP))
        self.assertEqual(called, ['http://172.18.0.2:8080/', 'ladyportfolio.auto.ikom.mg'])

    def test_running_unhealthy_candidate_not_destroyed(self):
        with patch.object(worker, 'is_running', return_value=True), \
             patch.object(worker, 'health', return_value=False), \
             patch.object(worker, 'docker') as docker:
            with self.assertRaises(worker.WorkerError):
                worker.install_app(APP)
            docker.assert_not_called()

    def test_run_keeps_internal_network_without_publish(self):
        calls = []
        def fake_docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('container', 'inspect') or args[:2] == ('network', 'inspect'):
                return SimpleNamespace(returncode=1, stdout='')
            return SimpleNamespace(returncode=0, stdout='')
        with patch.object(worker, 'is_running', return_value=False), \
             patch.object(worker, 'pull'), \
             patch.object(worker, 'docker', side_effect=fake_docker), \
             patch.object(worker, 'health', return_value=True):
            worker.install_app(APP)
        self.assertIn(('network', 'create', '--internal', '--driver', 'bridge', worker.network_name(APP)), calls)
        launch = next(call for call in calls if call[0] == 'run')
        self.assertNotIn('--publish', launch)
        self.assertEqual(launch[launch.index('--network') + 1], worker.network_name(APP))
        self.assertIn('--cap-drop', launch)

    def test_nginx_targets_real_container_ip(self):
        with patch.object(worker, 'container_ipv4', return_value='172.18.0.2'):
            config = worker.render_nginx([APP])
        self.assertIn('proxy_pass http://172.18.0.2:8080;', config)
        self.assertNotIn('35309', config)
        self.assertIn('location ^~ /__autokom_health/ { proxy_pass http://127.0.0.1:18080; }', config)

    def test_health_service_targets_container_ip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'routed.json'
            path.write_text(json.dumps({ENV: {'run_id': RUN, 'container_ip': '172.18.0.2',
                                               'container_port': 8080, 'host': APP['hosts'][0],
                                               'healthcheck': '/'}}))
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): return False
            class Opener:
                def open(self, request, timeout):
                    self.request = request
                    return Response()
            opener = Opener()
            with patch.object(health.urllib.request, 'build_opener', return_value=opener):
                self.assertTrue(health.healthy_app(ENV, RUN, path))
            self.assertEqual(opener.request.full_url, 'http://172.18.0.2:8080/')

    def test_health_service_rejects_public_ip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'routed.json'
            path.write_text(json.dumps({ENV: {'run_id': RUN, 'container_ip': '8.8.8.8',
                                               'container_port': 8080, 'host': APP['hosts'][0],
                                               'healthcheck': '/'}}))
            self.assertFalse(health.healthy_app(ENV, RUN, path))


class GitHubTests(TestCase):
    def test_named_account_used_without_switch(self):
        with patch.object(update.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='ghu_' + 'X'*50)) as run:
            self.assertEqual(update._gh_token_for_user('raph2611toky'), 'ghu_' + 'X'*50)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ['gh', 'auth', 'token', '--hostname', 'github.com', '--user', 'raph2611toky'])

    def test_named_account_failure_is_explicit(self):
        with patch.object(update.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaises(update.UpdateError):
                update._gh_token_for_user('raph2611toky')

    def test_gh_user_from_config_not_token(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'update.env'
            path.write_text('AUTOKOM_UPDATE_GITHUB_CLIENT_ID=Ivtest000\nAUTOKOM_UPDATE_GITHUB_USER=monlogin\n')
            self.assertEqual(update._config_value('AUTOKOM_UPDATE_GITHUB_USER', path), 'monlogin')

    def test_device_fallback_when_user_unset(self):
        with patch.object(update, '_config_value', return_value=''), \
             patch.dict(update.os.environ, {}, clear=True):
            self.assertEqual(update._gh_user(), '')


if __name__ == '__main__':
    main()
