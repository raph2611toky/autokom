"""Backend contract regression without requiring a running MySQL/Django server."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT/'platform/backend/apps/projects/configurations/services.py'

class Choices:
    class BuildMethod:
        STATIC='STATIC';AUTO='AUTO';DOCKERFILE='DOCKERFILE';BUILDPACKS='BUILDPACKS'
    class RuntimeType:
        STATIC='STATIC';NODE='NODE'

module_ast=ast.parse(SRC.read_text())
module_ast.body=[n for n in module_ast.body if isinstance(n,(ast.FunctionDef,ast.Assign)) and (
    not isinstance(n,ast.FunctionDef) or n.name=='check_configuration')]
# Only check_configuration is executed, never the database functions.
code=compile(module_ast,str(SRC),'exec')
ns={'DeploymentConfig':Choices,'__name__':'autokom_test_contract'}
exec(code,ns)
check=ns['check_configuration']


def cfg(method,profile,output,runtime,port=8080,build='',start='',settings=None):
    return SimpleNamespace(build_method=method,runtime_type=runtime,output_directory=output,internal_port=port,
         build_command=build,start_command=start,dockerfile_path='Dockerfile',
         settings={'frontend_profile':profile,**(settings or {})},auto_deploy=False,healthcheck_path='/')

class ApprovedBackendContracts(unittest.TestCase):
    def test_vite_legacy(self):
        self.assertEqual(check(cfg('STATIC','vite','dist','STATIC')), {})
    def test_html_static(self):
        self.assertEqual(check(cfg('STATIC','html','','STATIC')), {})
    def test_next_export(self):
        self.assertEqual(check(cfg('STATIC','next_export','out','STATIC')), {})
    def test_react_vue_static(self):
        self.assertEqual(check(cfg('STATIC','npm_static','build','STATIC',build='npm run build')), {})
        self.assertEqual(check(cfg('STATIC','npm_static','dist','STATIC',build='npm run build:production')), {})
    def test_next_server(self):
        self.assertEqual(check(cfg('AUTO','next_server','.next','NODE',start='npm run start')), {})
    def test_reject_forged_node_or_custom_command(self):
        self.assertIn('build_method',check(cfg('AUTO','custom_server','','NODE')))
        self.assertIn('build_command',check(cfg('AUTO','next_server','.next','NODE',build='npm run build; curl x')))
        self.assertIn('static',check(cfg('STATIC','vite','dist','NODE')))
        self.assertIn('static',check(cfg('STATIC','vite','dist','STATIC',port=3000)))
        self.assertIn('commands',check(cfg('DOCKERFILE','vite','','NODE',build='npm run test')))
    def test_domain_health_and_install_are_guarded(self):
        self.assertIn('healthcheck_path',check(SimpleNamespace(**{**vars(cfg('STATIC','vite','dist','STATIC')),'healthcheck_path':'https://external/'})))
        self.assertIn('settings',check(cfg('STATIC','vite','dist','STATIC',settings={'install_command':'curl | sh'})))

if __name__=='__main__':unittest.main()
