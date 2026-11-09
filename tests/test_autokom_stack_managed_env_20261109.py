"""Pure unit tests of the stack generated .env contract without Django settings/network."""
import ast
import pathlib
import unittest

SOURCE=pathlib.Path(__file__).resolve().parents[1]/'platform/backend/apps/projects/configurations/stack_services.py'
TREE=ast.parse(SOURCE.read_text())


class MemoryValue:
    def __init__(self,key,value='',secret_id=None):
        self.key,self.value,self.secret_id=key,value,secret_id
    def save(self,update_fields=None):
        pass


class FakeObjects:
    def __init__(self):
        self.data={}
    def filter(self,environment,key,secret__isnull=None):
        objects=self
        class Result:
            def delete(self):
                if key in objects.data and objects.data[key].secret_id is None:
                    del objects.data[key]
        return Result()
    def get_or_create(self,environment,key):
        existed=key in self.data
        if not existed:
            self.data[key]=MemoryValue(key)
        return self.data[key],not existed


class FakeEnvironmentVariable:
    objects=FakeObjects()


class ManagedEnvContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names={'_frontend_api_key','_sync_variables'}
        functions=[node for node in TREE.body if isinstance(node,ast.FunctionDef) and node.name in names]
        assert len(functions)==2
        cls.scope={'EnvironmentVariable':FakeEnvironmentVariable}
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(SOURCE),'exec'),cls.scope)

    def setUp(self):
        FakeEnvironmentVariable.objects=FakeObjects()

    def test_auto_backend_port_fixed_and_origin_updates_on_host_change(self):
        variables=FakeEnvironmentVariable.objects
        sync=self.scope['_sync_variables']
        sync(None,'env',{'profile':'node_express','origin_env_key':'FRONTEND_ORIGIN','variables':{}},
             'backend',api_url='site.old.example')
        self.assertEqual(variables.data['PORT'].value,'8000')
        self.assertEqual(variables.data['FRONTEND_ORIGIN'].value,'https://site.old.example')
        sync(None,'env',{'profile':'node_express','origin_env_key':'CORS_ORIGIN','variables':{}},
             'backend',api_url='site.new.example',previous_managed_key='FRONTEND_ORIGIN')
        self.assertNotIn('FRONTEND_ORIGIN',variables.data)
        self.assertEqual(variables.data['PORT'].value,'8000')
        self.assertEqual(variables.data['CORS_ORIGIN'].value,'https://site.new.example')
        self.assertEqual(variables.data['NODE_ENV'].value,'production')

    def test_frontend_api_name_renaming_purges_stale_generated_value(self):
        variables=FakeEnvironmentVariable.objects
        sync=self.scope['_sync_variables']
        sync(None,'env',{'profile':'next_server','api_env_key':'NEXT_PUBLIC_API_URL','variables':{'NEXT_PUBLIC_TITLE':'App'}},
             'frontend',api_url='backend.old.example')
        sync(None,'env',{'profile':'next_server','api_env_key':'NEXT_PUBLIC_BACKEND_URL','variables':{'NEXT_PUBLIC_TITLE':'App'}},
             'frontend',api_url='backend.new.example',previous_managed_key='NEXT_PUBLIC_API_URL')
        self.assertEqual(variables.data['NEXT_PUBLIC_BACKEND_URL'].value,'https://backend.new.example/api')
        self.assertNotIn('NEXT_PUBLIC_API_URL',variables.data)
        self.assertEqual(variables.data['NEXT_PUBLIC_TITLE'].value,'App')

    def test_existing_plain_variables_untouched_on_origin_rename(self):
        variables=FakeEnvironmentVariable.objects
        variables.data['APP_MODE']=MemoryValue('APP_MODE','release')
        self.scope['_sync_variables'](None,'env',{'profile':'python_flask','origin_env_key':'CORS_ORIGIN',
            'variables':{}},'backend',api_url='site.new.example',previous_managed_key='FRONTEND_ORIGIN')
        self.assertEqual(variables.data['APP_MODE'].value,'release')
        self.assertEqual(variables.data['PORT'].value,'8000')


if __name__=='__main__':
    unittest.main()
