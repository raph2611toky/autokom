"""No network or Django required: verify trusted Vite BuildKit recipe and lifecycle."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('autokom_static_agent',ROOT/'scripts/runtime/autokom_agent_local.py')
agent=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)

class StaticAgentTests(unittest.TestCase):
    def test_static_dockerfile_generated_by_platform_not_from_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            ws=base/'repo'; ws.mkdir(); (ws/'.git').mkdir()
            (ws/'package.json').write_text(json.dumps({'scripts':{'build':'vite build'},'devDependencies':{'vite':'*'}}))
            (ws/'package-lock.json').write_text('{}')
            artifacts=base/'artifacts'; artifacts.mkdir()
            seen=[]
            def mock_execute(argv,timeout=0):
                seen.append(argv)
                assert 'buildctl' == argv[0]
                recipe=Path(next(arg.partition('=')[2] for arg in argv if arg.startswith('dockerfile=')))/'Dockerfile'
                contents=recipe.read_text()
                assert 'npm ci --ignore-scripts' in contents
                assert 'nginx-unprivileged' in contents
                assert '--local' in argv
                dest=Path(next(x.split('dest=')[1] for x in argv if 'dest=' in x))
                dest.write_bytes(b'0'*2048)
            doc={'run_id':'11111111-1111-4111-8111-111111111111','build':{
              'root':'','method':'STATIC','runtime':'STATIC','output_directory':'dist','port':8080,'dockerfile':'Dockerfile','start_command':'','build_command':''}}
            with patch.dict(os.environ,{'WORKSPACE':str(ws),'AUTOKOM_ARTIFACT_DIR':str(artifacts)}),patch.object(agent,'buildkit_preflight'),patch.object(agent,'execute',mock_execute):
                agent.build(doc)
            self.assertEqual(len(seen),1)
            self.assertTrue((artifacts/(doc['run_id']+'.tar')).exists())
    def test_other_static_port_refused_before_build(self):
        with tempfile.TemporaryDirectory() as folder:
            ws=Path(folder); (ws/'.git').mkdir()
            doc={'run_id':'11111111-1111-4111-8111-111111111111','build':{
              'root':'','method':'STATIC','runtime':'STATIC','output_directory':'dist','port':8000,'dockerfile':'Dockerfile'}}
            with patch.dict(os.environ,{'WORKSPACE':str(ws),'AUTOKOM_ARTIFACT_DIR':str(ws)}):
                with self.assertRaises(SystemExit):agent.build(doc)
    def test_backend_deploy_seeds_free_plan_after_migration(self):
        manager=(ROOT/'scripts/lib/autokom_app_manager.py').read_text()
        self.assertLess(manager.index("'migrate', '--noinput'"),manager.index("'seed_project_offers'"))

if __name__ == '__main__': unittest.main()
