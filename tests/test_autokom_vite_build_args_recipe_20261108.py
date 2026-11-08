import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agent_vite', ROOT/'scripts/runtime/autokom_agent_local.py')
agent=importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class ViteRecipeEnvTests(unittest.TestCase):
    def test_vite_public_build_vars_used_without_shell_or_secret_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'repo';source.mkdir();(source/'.git').mkdir()
            (source/'package.json').write_text(json.dumps({'scripts':{'build':'vite build'},'devDependencies':{'vite':'*'}}))
            (source/'package-lock.json').write_text('{}')
            artifacts=root/'artifacts';artifacts.mkdir()
            doc={'run_id':'11111111-1111-4111-8111-111111111111',
                 'public_build_variables':{'VITE_SITE_NAME':'Portfolio','VITE_PUBLIC_URL':'https://example.com'},
                 'build':{'root':'','method':'STATIC','runtime':'STATIC','output_directory':'dist','port':8080,
                          'dockerfile':'Dockerfile','start_command':'','build_command':''}}
            def mock_exec(argv,timeout=0):
                self.assertEqual(argv[0],'buildctl')
                self.assertIn('build-arg:VITE_SITE_NAME=Portfolio',argv)
                file=Path(next(x.split('=',1)[1] for x in argv if x.startswith('dockerfile=')))/'Dockerfile'
                recipe=file.read_text()
                self.assertIn('ARG VITE_SITE_NAME\n',recipe)
                self.assertNotIn('Portfolio',recipe)
                self.assertIn('RUN npm run build',recipe)
                Path(next(x.split('dest=',1)[1] for x in argv if 'dest=' in x)).write_bytes(b'a'*2048)
            with patch.dict(os.environ,{'WORKSPACE':str(source),'AUTOKOM_ARTIFACT_DIR':str(artifacts)}), \
                 patch.object(agent,'buildkit_preflight'),patch.object(agent,'execute',mock_exec):
                agent.build(doc)
            self.assertTrue((artifacts/(doc['run_id']+'.tar')).exists())

if __name__=='__main__':unittest.main()
