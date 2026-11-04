"""Stdlib tests of deployment configuration vs the implemented Jenkins runner."""
from pathlib import Path
from types import SimpleNamespace
import ast
import unittest

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / 'apps/projects/configurations/services.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FN = next(node for node in TREE.body
          if isinstance(node, ast.FunctionDef) and node.name == 'check_configuration')
FAKE_MODEL = SimpleNamespace(BuildMethod=SimpleNamespace(DOCKERFILE='DOCKERFILE'))
ENV = {'DeploymentConfig': FAKE_MODEL}
exec(compile(ast.fix_missing_locations(ast.Module(body=[FN], type_ignores=[])),
             '<test_config>', 'exec'), ENV)
check_configuration = ENV['check_configuration']


def config(**overrides):
    data = dict(build_method='DOCKERFILE', dockerfile_path='Dockerfile',
                build_command='', start_command='', settings={},
                auto_deploy=False, auto_deploy_branch='', branch='main',
                healthcheck_path='/health/')
    data.update(overrides)
    return SimpleNamespace(**data)


class RunnerCompatibilityTests(unittest.TestCase):
    def test_supported_dockerfile_is_finalizable(self):
        self.assertEqual(check_configuration(config()), {})

    def test_modes_not_implemented_by_runner_cannot_finalize(self):
        for method in ('AUTO', 'STATIC', 'BUILDPACKS'):
            with self.subTest(method=method):
                self.assertIn('build_method', check_configuration(config(build_method=method)))

    def test_custom_commands_fail_before_jenkins(self):
        self.assertIn('commands', check_configuration(config(build_command='npm run build')))
        self.assertIn('commands', check_configuration(config(start_command='npm start')))

    def test_unused_install_command_must_not_be_silently_accepted(self):
        self.assertIn('settings', check_configuration(config(settings={'install_command':'pip install -r requirements.txt'})))

    def test_healthcheck_matches_broker_restrictions(self):
        for path in ('/health?debug=1', '/a#frag', '/%2f', '/a\\b', '', 'health'):
            with self.subTest(path=path):
                self.assertIn('healthcheck_path', check_configuration(config(healthcheck_path=path)))
        self.assertNotIn('healthcheck_path', check_configuration(config(healthcheck_path='/api/health/')))

    def test_auto_deploy_has_branch(self):
        self.assertIn('auto_deploy_branch', check_configuration(config(auto_deploy=True, branch='', auto_deploy_branch='')))


if __name__ == '__main__':
    unittest.main()
