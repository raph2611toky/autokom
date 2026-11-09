"""Regression contract for inherited Next.js outputs in two-service deployments.

Extract only the serializer method so tests run without Django or a database,
while executing the exact validation implementation from the source tree.
"""
import ast
import pathlib
import re
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/backend/apps/projects/configurations/stack_services.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf8'))
SERIALIZER = next(node for node in TREE.body if isinstance(node, ast.ClassDef)
                  and node.name == 'StackComponentSerializer')
VALIDATE = next(node for node in SERIALIZER.body if isinstance(node, ast.FunctionDef)
                and node.name == 'validate')

class SerializerError(Exception):
    pass


def validate_component(kind, profile, output):
    scope = {
        'FRONT_PROFILES': {'vite','npm_static','html','next_server','next_export'},
        'BACKEND_RUNTIMES': {'node_express':'NODE','python_fastapi':'PYTHON'},
        'MANAGED_KEY': re.compile(r'^[A-Z][A-Z0-9_]{0,99}$'),
        'ENV_KEY': re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,159}$'),
        'PUBLIC_PREFIXES': ('VITE_','REACT_APP_','VUE_APP_','NEXT_PUBLIC_'),
        'DANGEROUS_KEYS': ('PASSWORD','SECRET','TOKEN','PRIVATE_KEY','API_KEY','DATABASE_URL','REDIS_URL'),
        'serializers': types.SimpleNamespace(ValidationError=SerializerError),
    }
    functions = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('_valid_managed_name','_frontend_api_key')]
    scope_module=ast.fix_missing_locations(ast.Module(body=[*functions,VALIDATE],type_ignores=[]))
    exec(compile(scope_module,str(SOURCE),'exec'),scope)
    attrs={'profile':profile, 'output_directory':output, 'variables':{}, 'secrets':{}}
    return scope['validate'](types.SimpleNamespace(context={'kind':kind}),attrs)


class NextStackOutputTests(unittest.TestCase):
    def test_legacy_next_build_folder_accepted(self):
        for old in ('','.next','build'):
            with self.subTest(value=old):
                self.assertEqual(validate_component('frontend','next_server',old)['output_directory'],old)

    def test_only_compatible_legacy_output_accepted(self):
        for kind,profile,output in [
            ('frontend','next_server','dist'),
            ('frontend','npm_static','.next'),
            ('frontend','vite','.next'),
            ('frontend','next_export','.next'),
            ('frontend','html','.next'),
            ('backend','node_express','.next'),
            ('backend','node_express','dist'),
        ]:
            with self.subTest(profile=profile,output=output):
                with self.assertRaises(SerializerError):
                    validate_component(kind,profile,output)

    def test_other_supported_outputs(self):
        cases=[('frontend','npm_static','dist'),('frontend','npm_static','build'),
               ('frontend','next_export','out'),('frontend','next_export',''),
               ('frontend','vite','dist'),('frontend','html',''),
               ('backend','node_express',''),('backend','node_express','build')]
        for kind,profile,output in cases:
            with self.subTest(profile=profile,output=output):
                self.assertEqual(validate_component(kind,profile,output)['output_directory'],output)

    def test_serializer_accepts_legacy_options_before_validation(self):
        entry=next(n for n in SERIALIZER.body if isinstance(n,ast.Assign)
                   and any(isinstance(target,ast.Name) and target.id=='output_directory' for target in n.targets))
        kwargs={arg.arg:arg.value for arg in entry.value.keywords}
        self.assertTrue({'dist','build','.next','out',''}.issubset({n.value for n in kwargs['choices'].elts}))
        self.assertTrue(kwargs['allow_blank'].value)
        src=SOURCE.read_text()
        self.assertIn("'next_server': ''",src)
        self.assertIn("cfg.output_directory = (data.get('output_directory', 'build') if profile == 'npm_static'",src)

if __name__=='__main__':
    unittest.main()
