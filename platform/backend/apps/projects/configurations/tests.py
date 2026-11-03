from django.test import TestCase
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.projects.configurations.serializers import DeploymentConfigWriteSerializer
from apps.projects.common import safe_relative_path


class BuildValidationTests(TestCase):
    def test_reject_path_traversal(self):
        for path in ('../../etc', '/etc', 'a/../b'):
            with self.assertRaises(ValidationError):
                safe_relative_path(path)

    def test_runtime_choices_and_custom_build_fields(self):
        serializer = DeploymentConfigWriteSerializer(data={
            'runtime_type': 'PYTHON', 'build_method': 'AUTO',
            'branch': 'main', 'root_directory': 'backend',
            'build_command': 'pip install -r requirements.txt', 'start_command': 'gunicorn app.wsgi',
            'internal_port': 8000,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_prevent_unknown_settings(self):
        serializer = DeploymentConfigWriteSerializer(data={'settings': {'run_as_root': True}})
        self.assertFalse(serializer.is_valid())
