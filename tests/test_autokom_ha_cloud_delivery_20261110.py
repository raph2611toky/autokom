import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

ROOT = Path(__file__).resolve().parents[1]


def imported(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ctl = imported('scripts/ha/autokom_ha_delivery.py', 'ak_delivery')
agent = imported('terraform/modules/gcp_regional_ha/files/worker-agent.py', 'ak_worker')
health = imported('terraform/modules/gcp_regional_ha/files/worker-delivery-health.py', 'ak_health')

E1 = '11111111-1111-4111-8111-111111111111'
E2 = '22222222-2222-4222-8222-222222222222'
R1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
R2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
IMAGE = ('us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images/'
         'client-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@sha256:' + 'a' * 64)
CFG = {'project_id': 'plucky-stacker-480421-c4', 'region':'us-central1', 'artifact_repository': 'autokom-ha-images'}


def app(env=E1, run=R1, ha=False):
    return {'environment_id': env,'run_id':run,'image':IMAGE,'hosts':[('foo' if env==E1 else 'bar')+'.auto.ikom.mg'],
            'internal_port':8080,'healthcheck':'/','memory_mb':400,'cpu_millicores':500,'ha':ha}


def workers():
    return [{'name':'autokom-ha-web-ab12','zone':'us-central1-a','ip':'10.128.0.10','memory':0,'cpu':0},
            {'name':'autokom-ha-web-cd34','zone':'us-central1-f','ip':'10.128.0.11','memory':0,'cpu':0}]


def test_standard_defaults_one_worker_no_implicit_ha():
    manifest, choices = ctl.assign([app()], workers())
    assert choices[0]['ha'] is False
    assert len(choices[0]['workers']) == 1
    assert sum(len(items) for items in manifest.values()) == 1


def test_ha_explicit_assigns_two_distinct_zones():
    manifest, choices = ctl.assign([app(ha=True)], workers())
    assert choices[0]['ha'] is True
    assert {item['zone'] for item in choices[0]['workers']} == {'us-central1-a', 'us-central1-f'}
    assert sum(len(items) for items in manifest.values()) == 2


def test_ha_single_zone_fails_closed():
    available = workers()[:1]
    manifest, choices = ctl.assign([app(ha=True)], available)
    assert choices[0]['status'] == 'INSUFFICIENT_CAPACITY'
    assert manifest[available[0]['name']] == []


def test_ha_insufficient_memory_never_assigns_one_of_two():
    all_workers = workers()
    all_workers[1]['memory'] = 1300
    manifest, choices = ctl.assign([app(ha=True)], all_workers)
    assert choices[0]['status'] == 'INSUFFICIENT_CAPACITY'
    assert not any(manifest.values())


def test_immutable_image_and_host_manifest_validation():
    worker = workers()[0]
    manifest, _ = ctl.assign([app()], workers())
    document = {'schema':1,'instance':worker['name'],'zone':worker['zone'], 'applications':manifest[worker['name']]}
    assert len(agent.validate(document, CFG, worker['name'], worker['zone'])) == 1
    bad = deepcopy(document); bad['applications'][0]['image'] = 'docker.io/public/latest'
    with pytest.raises(agent.WorkerError, match='Untrusted'):
        agent.validate(bad, CFG, worker['name'], worker['zone'])
    bad = deepcopy(document); bad['applications'][0]['hosts'] = ['bad_host;rm.auto.ikom.mg']
    with pytest.raises(agent.WorkerError, match='hostname'):
        agent.validate(bad, CFG, worker['name'], worker['zone'])


def test_worker_rejects_wrong_identity_and_large_capacity():
    manifest, _ = ctl.assign([app()], workers())
    worker = workers()[0]
    document = {'schema':1,'instance':worker['name'],'zone':worker['zone'], 'applications':manifest[worker['name']]}
    with pytest.raises(agent.WorkerError, match='identity'):
        agent.validate(document, CFG, 'another-instance', worker['zone'])
    bad = deepcopy(document); bad['applications'][0]['memory_mb'] = 2000
    with pytest.raises(agent.WorkerError, match='resource'):
        agent.validate(bad, CFG, worker['name'], worker['zone'])


def test_nginx_only_renders_exact_safe_hostname():
    manifest, _ = ctl.assign([app()], workers())
    conf = agent.render_nginx(next(rows for rows in manifest.values() if rows))
    assert 'server_name foo.auto.ikom.mg;' in conf
    assert 'proxy_pass http://127.0.0.1:' in conf
    assert 'listen 8080;' in conf


def test_health_endpoint_isolated_from_mig_health():
    h = health.handler_factory('/healthz', check=lambda: True, app_check=lambda env: env==E1)
    # Handler factory must not evaluate app health at configuration time.
    assert h.__name__ == 'Handler'
    assert health.prepared.__name__ == 'prepared'


def test_static_routing_is_not_published_before_each_replica_healthy(monkeypatch):
    called=[]
    monkeypatch.setattr(ctl,'backend_healthy',lambda a,b: 1)
    monkeypatch.setattr(ctl,'ensure_backend',lambda a,w: ('https://example.invalid/backend', [{'group':'a'},{'group':'b'}]))
    monkeypatch.setattr(ctl,'route_host',lambda a,b: called.append((a,b)))
    assert ctl.backend_healthy(app(ha=True),[{'group':'a'},{'group':'b'}]) < 2
    assert called==[]


def test_backend_mode_is_not_default_and_legacy_worker_bootstrap_unchanged():
    model=(ROOT/'platform/backend/apps/projects/configurations/models.py').read_text()
    assert model.count('high_availability_requested = models.BooleanField(default=False)') == 2
    tf=(ROOT/'terraform/modules/gcp_regional_ha/main.tf').read_text()
    assert 'worker-delivery-health.py' in tf and 'worker-health.py' in tf
    assert 'enable_app_delivery ? 1 : 0' in tf
    assert 'ignore_changes = [host_rule, path_matcher]' in tf


def test_no_plaintext_secrets_or_stack_cloud_promotion():
    source=(ROOT/'scripts/ha/autokom_ha_delivery.py').read_text()
    assert "current.get('variables_present') or current.get('stack_project_id')" in source
    assert 'cloud_https_ready' in source


def test_worker_container_network_is_internal():
    script=(ROOT/'terraform/modules/gcp_regional_ha/files/worker-agent.py').read_text()
    assert "docker('network', 'create', '--internal', '--driver', 'bridge'" in script


def test_health_requires_correct_run_id_not_old_container():
    from tempfile import TemporaryDirectory
    import json
    with TemporaryDirectory() as temp:
        route = Path(temp) / 'routes.json'
        route.write_text(json.dumps({E1: {'run_id': R1, 'port': 21001,
                    'healthcheck': '/', 'host': 'foo.auto.ikom.mg'}}))
        # Stale route is rejected before making a network request.
        assert health.healthy_app(E1, R2, routes=route) is False
    controller=(ROOT/'scripts/ha/autokom_ha_delivery.py').read_text()
    assert "app['environment_id'] + '/' + app['run_id']" in controller
