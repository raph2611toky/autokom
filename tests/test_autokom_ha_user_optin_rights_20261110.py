"""Source-level safety checks. Django database tests run on the production venv."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_no_implicit_pro_ha_and_explicit_back_end_guard():
    price = (ROOT/'platform/frontend/src/config/pricing.js').read_text()
    assert 'highAvailability: false,' in price
    assert "highAvailability: plan.id === 'pro'" not in price
    service = (ROOT/'platform/backend/apps/projects/configurations/services.py').read_text()
    assert 'if getattr(cfg, \'high_availability_requested\', False):' in service
    assert 'if granted.max_instances < 2:' in service
    assert 'HighAvailabilityOption.Status.ACTIVE' in service
    assert 'Désactivez l’option HA pour déployer en standard' in service


def test_admin_can_create_option_for_a_selected_environment():
    admin = (ROOT/'platform/backend/apps/plans/entitlements/admin.py').read_text()
    assert 'def get_readonly_fields(self, request, obj=None):' in admin
    assert "('environment',) if obj else ()" in admin
    assert 'obj.full_clean()' in admin
    ast.parse(admin)


def test_rights_doctor_cannot_grant_paid_rights():
    file = ROOT/'platform/backend/apps/plans/entitlements/management/commands/autokom_ha_rights.py'
    tree = ast.parse(file.read_text())
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert any(n.name == 'handle' for n in functions)
    forbidden = {'create', 'update', 'delete', 'save', 'bulk_create', 'update_or_create', 'get_or_create'}
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not forbidden.intersection(calls)
    client = (ROOT/'scripts/lib/autokom_ha_admin.py').read_text()
    assert 'if os.geteuid() != 0:' in client
    assert "'autokom_ha_rights'" in client
