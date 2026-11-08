import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_build_env', ROOT/'platform/backend/apps/deployments/runs/public_build_env.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec_agent = importlib.util.spec_from_file_location('autokom_agent_public', ROOT/'scripts/runtime/autokom_agent_local.py')
agent = importlib.util.module_from_spec(spec_agent)
spec_agent.loader.exec_module(agent)


class PublicViteBuildEnvTest(unittest.TestCase):
    def test_public_only_sorted(self):
        rows=[('VITE_URL','https://example.org',None),('DATABASE_URL','postgres://private',None),('VITE_TITLE','My site',None)]
        self.assertEqual(list(module.public_vite_variables(rows)),['VITE_TITLE','VITE_URL'])
    def test_secrets_never_forwarded(self):
        for rows in [[('VITE_API_TOKEN','x',None)],[('VITE_URL','x','secret-id')],[('VITE_URL','x\nother',None)]]:
            with self.assertRaises(ValueError):module.public_vite_variables(rows)
    def test_buildkit_args_and_declared_arg(self):
        declarations,args=agent.public_vite_build_args({'public_build_variables':{'VITE_API_URL':'https://example.org','VITE_TITLE':'Portfolio'}})
        self.assertIn('ARG VITE_API_URL\n',declarations)
        self.assertIn('build-arg:VITE_API_URL=https://example.org',args)
        self.assertNotIn('DATABASE_URL',declarations)
    def test_runner_refuses_malformed_values(self):
        for values in [{'VITE_SECRET':'x'}, {'VITE_TITLE':'x\nRUN whoami'}, {'FOO':'x'}]:
            with self.assertRaises(SystemExit):agent.public_vite_build_args({'public_build_variables':values})

if __name__=='__main__':unittest.main()
