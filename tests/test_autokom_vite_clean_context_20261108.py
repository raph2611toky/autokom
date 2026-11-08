"""Regression: a GitHub repository's node_modules must not overwrite npm ci."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_clean_source', ROOT/'scripts/runtime/autokom_agent_local.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def document(method='STATIC'):
    return {
        'run_id': '11111111-1111-4111-8111-111111111111',
        'public_build_variables': {'VITE_PUBLIC_URL': 'https://example.org'},
        'build': {'root': '', 'method': method, 'runtime': 'STATIC',
                  'output_directory': 'dist', 'port': 8080, 'dockerfile': 'Dockerfile',
                  'start_command': '', 'build_command': ''},
    }


class ViteSourceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root/'repo'
        self.source.mkdir()
        (self.source/'.git').mkdir()
        (self.source/'package.json').write_text(json.dumps({
            'scripts': {'build': 'vite build'},
            'devDependencies': {'vite': '^7.0.0'},
        }))
        (self.source/'package-lock.json').write_text('{}')
        (self.source/'src').mkdir()
        (self.source/'src'/'App.jsx').write_text('export default function App() {}')
        (self.source/'public').mkdir()
        (self.source/'public'/'logo.png').write_bytes(b'hello')
        (self.source/'node_modules'/'.bin').mkdir(parents=True)
        self.vite = self.source/'node_modules'/'.bin'/'vite'
        self.vite.write_text('#!/bin/sh\nexit 0')
        self.vite.chmod(0o644)  # Reproduces Jenkins #7: sh: vite: Permission denied.
        (self.source/'node_modules'/'some_pkg').mkdir()
        (self.source/'dist').mkdir()
        (self.source/'dist'/'index.html').write_text('stale')
        (self.source/'src'/'node_modules').mkdir()
        (self.source/'src'/'node_modules'/'old.bin').write_text('stale')
        self.artifacts = self.root/'artifacts'
        self.artifacts.mkdir()

    def test_buildkit_receives_clean_snapshot_and_preserves_checkout(self):
        inspected=[]
        def fake_execute(args, timeout=0):
            context = Path(next(v.split('=', 1)[1] for v in args if v.startswith('context=')))
            docker_dir = Path(next(v.split('=', 1)[1] for v in args if v.startswith('dockerfile=')))
            self.assertTrue(context.is_dir())
            self.assertFalse((context/'node_modules').exists())
            self.assertFalse((context/'.git').exists())
            self.assertFalse((context/'dist').exists())
            self.assertFalse((context/'src'/'node_modules').exists())
            self.assertEqual((context/'src'/'App.jsx').read_text(), 'export default function App() {}')
            self.assertEqual((context/'public'/'logo.png').read_bytes(), b'hello')
            self.assertEqual((context/'package-lock.json').read_text(), '{}')
            recipe=(docker_dir/'Dockerfile').read_text()
            self.assertIn('RUN npm ci --ignore-scripts --no-audit --no-fund', recipe)
            self.assertIn('RUN npm run build', recipe)
            self.assertIn('COPY . .',recipe)
            self.assertLess(recipe.index('RUN npm ci'),recipe.index('COPY . .'))
            self.assertEqual(args[0],'buildctl')
            self.assertIn('build-arg:VITE_PUBLIC_URL=https://example.org',args)
            output = Path(next(v.split('dest=', 1)[1] for v in args if v.startswith('type=docker,')))
            output.write_bytes(b'a'*2048)
            inspected.append(context)
        with patch.dict(os.environ, {'WORKSPACE':str(self.source), 'AUTOKOM_ARTIFACT_DIR':str(self.artifacts)}), \
             patch.object(agent, 'buildkit_preflight'), patch.object(agent,'execute',fake_execute):
            agent.build(document())
        self.assertEqual(len(inspected),1)
        self.assertFalse(inspected[0].exists(), 'Temporary build context must be removed')
        self.assertTrue(self.vite.exists(), 'Never change user GitHub checkout')
        self.assertFalse(os.access(self.vite,os.X_OK))
        self.assertTrue((self.artifacts/(document()['run_id']+'.tar')).exists())

    def test_temporary_context_removed_if_buildkit_fails(self):
        seen=[]
        def failing_execute(args, timeout=0):
            seen.append(Path(next(v.split('=',1)[1] for v in args if v.startswith('context='))))
            raise SystemExit(2)
        with patch.dict(os.environ, {'WORKSPACE':str(self.source),'AUTOKOM_ARTIFACT_DIR':str(self.artifacts)}), \
             patch.object(agent,'buildkit_preflight'),patch.object(agent,'execute',failing_execute):
            with self.assertRaises(SystemExit):
                agent.build(document())
        self.assertEqual(len(seen),1)
        self.assertFalse(seen[0].exists())
        self.assertFalse((self.artifacts/(document()['run_id']+'.tar')).exists())

    def test_custom_dockerfile_flow_not_changed(self):
        (self.source/'Dockerfile').write_text('FROM scratch\n')
        seen=[]
        def fake_execute(args,timeout=0):
            seen.append(args)
            self.assertIn('context='+str(self.source),args)
            output=Path(next(v.split('dest=',1)[1] for v in args if v.startswith('type=docker,')))
            output.write_bytes(b'a'*2048)
        with patch.dict(os.environ, {'WORKSPACE':str(self.source), 'AUTOKOM_ARTIFACT_DIR':str(self.artifacts)}), \
             patch.object(agent, 'buildkit_preflight'),patch.object(agent,'execute',fake_execute):
            agent.build(document('DOCKERFILE'))
        self.assertEqual(len(seen),1)


if __name__=='__main__':
    unittest.main()
