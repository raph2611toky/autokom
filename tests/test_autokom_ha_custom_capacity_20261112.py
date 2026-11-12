"""No live GCP calls: controller's isolation, allocation, capacity and cleanup."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/ha'))
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from autokom_ha_infra_upgrade import render_tfvars, validate_plan, UpgradeError

spec = importlib.util.spec_from_file_location('autokom_ha_delivery_new', ROOT / 'scripts/ha/autokom_ha_delivery.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)
E1 = '11111111-1111-4111-8111-111111111111'
E2 = '22222222-2222-4222-8222-222222222222'
E3 = '33333333-3333-4333-8333-333333333333'


def app(env=E1, ha=True, mem=1024, cpu=750):
    return {'environment_id': env, 'run_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'image': 'example', 'hosts': ['example.auto.ikom.mg'], 'internal_port': 8080,
            'memory_mb': mem, 'cpu_millicores': cpu, 'healthcheck': '/', 'ha': ha}


def nodes(count, new=True):
    return [{'name': f'autokom-ha-web-w{i}', 'zone': 'us-central1-a' if i%2==0 else 'us-central1-f',
             'ip': f'10.0.0.{i+1}', 'memory': 0, 'cpu': 0,
             'memory_limit': 3072 if new else 1500, 'cpu_limit': 1600 if new else 2000}
            for i in range(count)]


def test_two_ha_apps_share_two_custom_workers_without_overcommit():
    manifests, decisions = ctl.assign([app(E1), app(E2)], nodes(2))
    assert all(d['status'] == 'ASSIGNED' and len(d['workers']) == 2 for d in decisions)
    assert [len(v) for v in manifests.values()] == [2, 2]
    assert ctl.minimum_workers_for_demand([app(E1), app(E2)]) == 2


def test_third_application_triggers_capacity_floor_three():
    assert ctl.minimum_workers_for_demand([app(E1), app(E2), app(E3, ha=False)]) == 3
    _, decisions = ctl.assign([app(E1), app(E2), app(E3, ha=False)], nodes(2))
    assert decisions[-1]['status'] == 'INSUFFICIENT_CAPACITY'


def test_legacy_workers_remain_under_old_capacity_during_rolling_update():
    _, decisions = ctl.assign([app(E1), app(E2)], nodes(2, new=False))
    assert decisions[0]['status'] == 'ASSIGNED'
    assert decisions[1]['status'] == 'INSUFFICIENT_CAPACITY'


def test_zone_anti_affinity_in_third_party_placement():
    _, rows = ctl.assign([app(E1)], nodes(5))
    assert len({w['zone'] for w in rows[0]['workers']}) == 2


def test_scale_out_immediate_and_scale_in_waits_stable_30_minutes():
    result = ctl.desired_floor([app(E1), app(E2), app(E3, ha=False)], {}, now=1000,
                               autoscaler_min=2, autoscaler_max=6)
    assert result['target'] == 3 and result['status'] == 'SCALE_OUT_REQUESTED'
    first = ctl.desired_floor([app(E1)], {}, now=2000, autoscaler_min=4, autoscaler_max=6)
    assert first['target'] == 4 and first['lower_since'] == 2000
    second = ctl.desired_floor([app(E1)], {'capacity': first}, now=2100,
                               autoscaler_min=4, autoscaler_max=6)
    assert second['target'] == 4
    third = ctl.desired_floor([app(E1)], {'capacity': second}, now=3900,
                              autoscaler_min=4, autoscaler_max=6)
    assert third['target'] == 2 and third['status'] == 'SCALE_IN_REQUESTED'


def test_capacity_limit_reported_not_fake_assigned():
    expensive = [app('11111111-1111-4111-8111-%012d' % i, ha=True, cpu=1600) for i in range(5)]
    assert ctl.minimum_workers_for_demand(expensive, max_workers=6) is None


def test_patch_autoscaler_uses_only_minimum_and_waits_region_operation(monkeypatch):
    log=[]
    monkeypatch.setattr(ctl, 'api', lambda method, url, body: log.append((method,url,body)) or {'name':'operation-123'})
    monkeypatch.setattr(ctl, 'operation', lambda doc, scope: log.append((scope,doc)))
    assert ctl.apply_capacity_floor({'target':4}, 2)
    assert log[0][0] == 'PATCH' and 'autoscaler=autokom-ha-autoscaler' in log[0][1]
    assert log[0][2] == {'autoscalingPolicy':{'minNumReplicas':4}}
    assert log[1][0] == 'regions/us-central1'


def test_deleted_environment_cannot_be_unrouted_if_live(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl, 'STATE', tmp_path)
    (tmp_path / E1).mkdir()
    (tmp_path / E1 / 'current.json').write_text('{}')
    previous = {'applications':[{'environment':E1}, {'environment':E2}]}
    assert ctl.deleted_environments(previous, []) == [E2]
    assert ctl.deleted_environments(previous, [app(E2)]) == []


def test_remove_only_own_lb_url_rules(monkeypatch):
    target = 'akp-' + E2.replace('-', '')[:20]
    old = {'hostRules':[{'hosts':['live.auto.ikom.mg'], 'pathMatcher':'live'},
                        {'hosts':['gone.auto.ikom.mg'],'pathMatcher':target}],
           'pathMatchers':[{'name':'live','defaultService':'keep'},
                           {'name':target,'defaultService':'gone'}], 'fingerprint':'abc'}
    calls=[]
    monkeypatch.setattr(ctl, 'compute', lambda method,path,body=None: old if method == 'GET' else calls.append(body) or {'name':'op'})
    monkeypatch.setattr(ctl, 'operation', lambda result,scope: None)
    assert ctl.unroute_deleted([E2])
    assert calls[0]['hostRules'] == [old['hostRules'][0]]
    assert calls[0]['pathMatchers'] == [old['pathMatchers'][0]]


def test_upgrade_preserves_active_flags_and_rejects_unrelated_changes():
    sample = 'project_id = "project"\nbootstrap_mode = "worker"\nenable_app_delivery = true\nmachine_type = "e2-small"\n'
    transformed = render_tfvars(sample)
    assert 'machine_type = "e2-custom-2-4096"' in transformed
    assert 'boot_disk_size_gb = 70' in transformed
    assert 'bootstrap_mode = "worker"' in transformed
    assert 'enable_app_delivery = true' in transformed
    assert 'max_replicas = 5' in transformed
    plan = {'resource_changes':[{'address':'module.regional_web_ha.google_compute_instance_template.web',
                                 'change':{'actions':['delete','create']}}]}
    assert validate_plan(plan)
    try:
        validate_plan({'resource_changes':[{'address':'module.regional_web_ha.google_compute_global_address.lb',
                                            'change':{'actions':['delete']}}]})
    except UpgradeError:
        pass
    else:
        raise AssertionError('Unsafe plan must not be approved')
