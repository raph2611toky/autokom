"""Tests de structure ; sans requête à la base de données."""
import ast
import json
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent


class NestedAppsArchitectureTests(SimpleTestCase):
    def test_concrete_apps_have_independent_config(self):
        inventory = json.loads((ROOT / "model_inventory.json").read_text())
        self.assertEqual(len(inventory), 67)
        self.assertEqual(len(settings.LOCAL_APPS), 21)
        self.assertEqual(len({x["app"] for x in inventory}), 21)
        for config in settings.LOCAL_APPS:
            path, class_name = config.rsplit(".", 1)
            package = path.removesuffix(".apps")
            directory = ROOT / package.replace(".", "/")
            for filename in ("apps.py", "models.py", "admin.py", "views.py",
                             "serializers.py", "urls.py", "permissions.py", "services.py",
                             "tests.py", "migrations/__init__.py", "swagger/__init__.py"):
                self.assertTrue((directory / filename).is_file(), (package, filename))
            real_config = apps.get_app_config(next(x["app"] for x in inventory
                if x["module"].startswith(package + ".")))
            self.assertEqual(real_config.name, package)
            self.assertEqual(real_config.__class__.__name__, class_name)

    def test_every_model_has_str(self):
        for app_config in apps.get_app_configs():
            if app_config.name.startswith("apps."):
                for model in app_config.get_models():
                    self.assertIn("__str__", model.__dict__, model)

    def test_group_packages_are_not_installed_apps(self):
        for group in ("apps.projects", "apps.deployments",
                      "apps.infrastructure", "apps.assistance", "apps.accounts"):
            self.assertFalse(any(x == group or x.startswith(group + ".apps.") for x in settings.LOCAL_APPS))

    def test_only_mysql(self):
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.mysql")
