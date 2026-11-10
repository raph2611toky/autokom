from pathlib import Path
import importlib.util
import sys
import uuid

path = Path(__file__).resolve().parents[1] / 'scripts' / 'ha' / 'autokom_ha_placement.py'
spec = importlib.util.spec_from_file_location('autokom_ha_placement', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

ENV = str(uuid.UUID('3a741822-18dd-4888-9a1c-e89886ddc6af'))
NODES = [
    {'name': 'autokom-ha-web-2l7l', 'zone': 'us-central1-a', 'status': 'RUNNING'},
    {'name': 'autokom-ha-web-tls8', 'zone': 'us-central1-f', 'status': 'RUNNING'},
]


def test_standard_selects_exactly_one():
    selected = module.select_workers(ENV, NODES, high_availability=False)
    assert len(selected) == 1
    assert selected[0] in NODES


def test_ha_selects_two_distinct_zones():
    selected = module.select_workers(ENV, NODES, high_availability=True)
    assert len(selected) == 2
    assert len({n['zone'] for n in selected}) == 2


def test_selection_deterministic_independent_of_listing_order():
    assert module.select_workers(ENV, NODES, high_availability=True) == module.select_workers(
        ENV, list(reversed(NODES)), high_availability=True)


def test_ha_fails_closed_when_only_one_zone():
    from pytest import raises
    with raises(module.PlacementError, match='deux zones'):
        module.select_workers(ENV, NODES[:1], high_availability=True)


def test_unhealthy_worker_excluded_and_ha_blocked():
    from pytest import raises
    with raises(module.PlacementError, match='deux zones'):
        module.select_workers(ENV, [NODES[0], {**NODES[1], 'status':'STOPPED'}], high_availability=True)


def test_does_not_select_rogue_names():
    from pytest import raises
    with raises(module.PlacementError):
        module.select_workers(ENV, [{'name': 'instance-20261028-090021', 'zone': 'us-central1-a', 'status':'RUNNING'}], high_availability=False)


def test_same_zone_more_nodes_cannot_claim_ha():
    from pytest import raises
    with raises(module.PlacementError):
        module.select_workers(ENV, NODES[:1]+[{'name':'autokom-ha-web-third', 'zone':'us-central1-a','status':'RUNNING'}], high_availability=True)


def test_invalid_uuid_rejected():
    from pytest import raises
    with raises(module.PlacementError):
        module.select_workers('injection\n', NODES, high_availability=True)
