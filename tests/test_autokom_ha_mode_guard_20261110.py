"""No database needed: check the production decision gate in isolation.

Django integration tests still must be run on the actual project venv before deploy.
"""
import ast
from pathlib import Path
import types

ROOT=Path(__file__).resolve().parents[1]


def load_function(path, function, globals_):
    tree=ast.parse((ROOT/path).read_text())
    node=next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name==function)
    ns=dict(globals_)
    exec(compile(ast.Module(body=[node], type_ignores=[]),str(path),'exec'),ns)
    return ns[function]


class DeploymentConflict(Exception): pass


class FakeOption:
    class Status:
        ACTIVE='ACTIVE'
        EXPIRED='EXPIRED'
        REJECTED='REJECTED'
    objects=None


class FakeObjects:
    def __init__(self,option):self.option=option
    def filter(self, **kwargs):return self
    def first(self):return self.option


def test_standard_does_not_implicitly_enable_paid_ha_option():
    FakeOption.objects=FakeObjects(types.SimpleNamespace(status='ACTIVE'))
    f=load_function('platform/backend/apps/deployments/runs/services.py','effective_replicas',
                    {'HighAvailabilityOption':FakeOption,'DeploymentConflict':DeploymentConflict})
    assert f(object(),object(),types.SimpleNamespace(high_availability_requested=False))==1


def test_ha_pending_option_does_not_silently_deploy_single():
    FakeOption.objects=FakeObjects(types.SimpleNamespace(status='PENDING'))
    f=load_function('platform/backend/apps/deployments/runs/services.py','effective_replicas',
                    {'HighAvailabilityOption':FakeOption,'DeploymentConflict':DeploymentConflict})
    import pytest
    with pytest.raises(DeploymentConflict,match='option non active'):
        f(object(),object(),types.SimpleNamespace(high_availability_requested=True))


def test_ha_active_uses_one_local_safe_candidate_and_two_cloud_replicas():
    FakeOption.objects=FakeObjects(types.SimpleNamespace(status='ACTIVE', expires_at=None))
    f=load_function('platform/backend/apps/deployments/runs/services.py','effective_replicas',
                    {'HighAvailabilityOption':FakeOption,'DeploymentConflict':DeploymentConflict})
    import pytest
    # Local Docker is never asked to create two fake replicas on one host.
    assert f(object(),object(),types.SimpleNamespace(high_availability_requested=True)) == 1


def test_model_and_migration_include_ha_for_single_and_stack():
    model=(ROOT/'platform/backend/apps/projects/configurations/models.py').read_text()
    migration=(ROOT/'platform/backend/apps/projects/configurations/migrations/0005_deploymentconfig_ha_request.py').read_text()
    assert model.count('high_availability_requested = models.BooleanField(default=False)')==2
    assert "model_name='deploymentconfig'" in migration
    assert "model_name='servicestack'" in migration
