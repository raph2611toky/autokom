"""Tests for the safe publication queue + Grafana integration (no GCP mutations)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts/ha'))
sys.path.insert(0, str(BASE / 'scripts/lib'))

from autokom_ha_autopublish import _valid_uuid, _active_run, _safe_file, process_one, QueueError
from autokom_ha_observability import merge_prometheus, dashboard, JOB, ProvisionError
from autokom_ha_extra import health_by_instance, to_prometheus

RID = '11111111-1111-4111-8111-111111111111'
EID = '22222222-2222-4222-8222-222222222222'


def test_uuid_safe():
    assert _valid_uuid(RID) == RID
    with pytest.raises(QueueError):
        _valid_uuid('../etc/passwd')


def test_root_owned_queue_and_no_symlink(tmp_path):
    if __import__('os').geteuid() != 0:
        pytest.skip('Root owned queue test')
    path = tmp_path / (RID + '.json')
    path.write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    assert _safe_file(path)['run_id'] == RID
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    with pytest.raises(QueueError):
        _safe_file(link)


def test_active_run_release_binding(tmp_path):
    env = tmp_path / EID
    env.mkdir()
    (env/'current.json').write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    assert _active_run(EID, RID, tmp_path)
    assert not _active_run(EID, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', tmp_path)


def test_process_existing_digest_no_push(tmp_path, monkeypatch):
    if __import__('os').geteuid() != 0:
        pytest.skip('Root owned queue test')
    import autokom_ha_autopublish as a
    queue = tmp_path / 'queue'
    result = tmp_path / 'results'
    root = tmp_path / 'root'
    queue.mkdir()
    (root / EID).mkdir(parents=True)
    (root / EID / 'current.json').write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    entry = queue / (RID + '.json')
    entry.write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    monkeypatch.setattr(a, 'registry_manifest_digest', lambda _image, _token: 'sha256:' + 'a' * 64)
    monkeypatch.setattr(a, 'publish', lambda *_args, **_kwargs: pytest.fail('Must not repush existing digest'))
    value = process_one(entry, token='x', queue=queue, results=result, root=root)
    assert value.endswith('@sha256:' + 'a' * 64)
    assert not entry.exists()
    assert json.loads((result / (RID + '.json')).read_text())['image'] == value


def test_rolled_back_run_is_not_promoted(tmp_path, monkeypatch):
    if __import__('os').geteuid() != 0:
        pytest.skip('Root owned queue test')
    import autokom_ha_autopublish as a
    queue = tmp_path / 'queue'
    queue.mkdir()
    item = queue / (RID + '.json')
    item.write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    monkeypatch.setattr(a, 'registry_manifest_digest', lambda *_: pytest.fail('No remote call on rollback'))
    with pytest.raises(QueueError):
        process_one(item, token='x', queue=queue, results=tmp_path/'results', root=tmp_path/'empty')


def test_prometheus_job_insertion_idempotent():
    import yaml
    config = 'global:\n  scrape_interval: 30s\nscrape_configs:\n  - job_name: node\n    static_configs:\n      - targets: ["127.0.0.1:9100"]\n'
    once = merge_prometheus(config)
    twice = merge_prometheus(once)
    assert yaml.safe_load(once) == yaml.safe_load(twice)
    assert len([x for x in yaml.safe_load(twice)['scrape_configs'] if x.get('job_name') == 'autokom-gcp-ha']) == 1


def test_custom_prometheus_job_refused():
    import yaml
    txt = yaml.safe_dump({'scrape_configs': [{'job_name': 'autokom-gcp-ha', 'static_configs': []}]})
    with pytest.raises(ProvisionError):
        merge_prometheus(txt)


def test_grafana_dashboard_autoprovisioned_datasource():
    obj = dashboard('autokom-prometheus')
    assert '__inputs' not in obj
    assert obj['uid'] == 'autokom-gcp-ha-20261110'
    assert any('worker_cpu_utilization' in str(x) for x in obj['panels'])
    assert '${DS_PROMETHEUS}' not in json.dumps(obj)


def test_worker_health_mapping_and_metrics():
    statuses = health_by_instance({'backends': [{'status': {'healthStatus': [
        {'instance': 'projects/demo/zones/us-central1-a/instances/vm-a', 'healthState': 'HEALTHY'},
        {'instance': 'projects/demo/zones/us-central1-f/instances/vm-b', 'healthState': 'UNHEALTHY'}]}}]})
    assert statuses == {'vm-a': 'HEALTHY', 'vm-b': 'UNHEALTHY'}
    report = {'instances': [{'name': 'vm-a', 'zone': 'us-central1-a', 'status': 'RUNNING',
                             'health': 'HEALTHY', 'action': 'NONE', 'cpu_utilization': .4,
                             'network_receive_Bps': 4096.0}]}
    metrics = to_prometheus(report)
    assert 'autokom_ha_worker_healthy{instance="vm-a",zone="us-central1-a"} 1' in metrics
    assert 'autokom_ha_worker_cpu_utilization{instance="vm-a",zone="us-central1-a"} 0.400000' in metrics
    assert 'network_receive_Bps' in metrics


def test_existing_release_auto_discovered(tmp_path):
    if __import__('os').geteuid() != 0:
        pytest.skip('Root-owned state required')
    from autokom_ha_autopublish import discover_local_releases
    root = tmp_path / 'runtime'
    folder = root / EID
    folder.mkdir(parents=True)
    (folder / 'current.json').write_text(json.dumps({'run_id': RID, 'environment_id': EID}))
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    (artifacts / (RID + '.tar')).write_bytes(b'placeholder')
    queue, results = tmp_path / 'queue', tmp_path / 'result'
    assert discover_local_releases(root, queue, results, artifacts) == 1
    assert discover_local_releases(root, queue, results, artifacts) == 0
    assert json.loads((queue / (RID + '.json')).read_text())['environment_id'] == EID
    (queue / (RID + '.json')).unlink()
    (folder / 'previous.json').write_text('{}')
    assert discover_local_releases(root, queue, results, artifacts) == 0
