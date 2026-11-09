"""Static contract checks for existing single-backend stack conversion.

These tests do not replace the Django database integration tests on GCP.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/backend/apps/projects/configurations/stack_services.py'


def test_preserve_original_backend_variables_and_secrets():
    source = SOURCE.read_text(encoding='utf-8')
    ast.parse(source)
    assert '_primary_service_is_backend(frontend_env)' in source
    assert 'SecretReference.objects.filter(project=project, environment=frontend_env).update(' in source
    assert 'EnvironmentVariable.objects.filter(environment=frontend_env).update(' in source
    assert 'environment=backend_env' in source
    assert '@transaction.atomic' not in source  # existing transaction.atomic() encloses conversion
    assert 'with transaction.atomic():' in source


def test_frontend_origin_is_not_moved():
    source = SOURCE.read_text(encoding='utf-8')
    assert "if profile:\n        return profile in BACKEND_RUNTIMES" in source
    assert "return previous.runtime_type in ('PYTHON', 'JAVA', 'PHP')" in source
