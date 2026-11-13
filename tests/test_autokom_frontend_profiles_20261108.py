"""Trusted frontend profiles and isolated BuildKit recipe tests."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('autokom_multi_agent', ROOT/'scripts/runtime/autokom_agent_local.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
from scripts.runtime.autokom_frontend_recipes import (recipe, public_build_args, validate_profile_source,
    safe_output, script_name)

RUN = '11111111-1111-4111-8111-111111111111'


def document(profile, output, method='STATIC', build_command='', variables=None):
    return {'run_id': RUN, 'build': {'method':method,'runtime':'NODE' if method == 'AUTO' else 'STATIC',
            'profile':profile, 'root':'', 'dockerfile':'Dockerfile', 'port':8080,
            'output_directory':output, 'build_command':build_command, 'start_command':'npm run start' if method=='AUTO' else ''},
            'public_build_variables': variables or {}}


class ApprovedBuilds(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo';self.repo.mkdir()
        (self.repo/'.git').mkdir()
        self.artifacts=self.root/'artifacts'; self.artifacts.mkdir()
        (self.repo/'node_modules').mkdir()
        (self.repo/'node_modules'/'should-not-copy').write_text('bad')

    def pkg(self, dependencies, scripts=None):
        (self.repo/'package.json').write_text(json.dumps({'dependencies':dependencies, 'scripts':scripts or {'build':'vite build','start':'next start'}}))
        (self.repo/'package-lock.json').write_text('{}')

    def run_build(self, doc, inspect):
        def pretend(argv, timeout=0):
            context=Path(next(s.split('=',1)[1] for s in argv if s.startswith('context=')))
            dockerfile_dir=Path(next(s.split('=',1)[1] for s in argv if s.startswith('dockerfile=')))
            source=(dockerfile_dir/'Dockerfile').read_text()
            self.assertFalse((context/'node_modules').exists())
            inspect(source,argv,context)
            dest=Path(next(s.split('dest=',1)[1] for s in argv if s.startswith('type=docker,')))
            dest.write_bytes(b'a'*2048)
        with patch.dict(os.environ, {'WORKSPACE':str(self.repo), 'AUTOKOM_ARTIFACT_DIR':str(self.artifacts)}), \
             patch.object(agent,'buildkit_preflight'),patch.object(agent,'execute',pretend):
            agent.build(doc)

    def test_plain_html_no_npm(self):
        (self.repo/'index.html').write_text('<h1>OK</h1>')
        self.run_build(document('html',''), lambda src,args,context: (
            self.assertNotIn('RUN npm ci',src),
            self.assertIn('COPY --chown=10001:10001 --from=assets /site/ /usr/share/nginx/html/',src),
            self.assertIn('listen 8080', (context/'autokom-nginx.conf').read_text())))

    def test_static_html_does_not_publish_source_or_credentials(self):
        (self.repo/'index.html').write_text('<h1>OK</h1>')
        (self.repo/'.env').write_text('PRIVATE=secret')
        (self.repo/'Dockerfile').write_text('FROM scratch')
        (self.repo/'private.key').write_text('private')
        (self.repo/'readme.md').write_text('internal')
        (self.repo/'images').mkdir()
        (self.repo/'images'/'logo.svg').write_text('<svg/>')
        self.run_build(document('html',''), lambda src,args,context: (
            self.assertFalse((context/'.env').exists()),
            self.assertFalse((context/'Dockerfile').exists()),
            self.assertFalse((context/'private.key').exists()),
            self.assertFalse((context/'readme.md').exists()),
            self.assertTrue((context/'images'/'logo.svg').exists())))

    def test_vite_trusted_recipe_unchanged(self):
        self.pkg({'vite':'7.1.0'})
        self.run_build(document('vite','dist',variables={'VITE_APP':'demo'}), lambda src,args,context: (
            self.assertIn('RUN npm ci --ignore-scripts',src),
            self.assertIn('RUN npm run build',src),
            self.assertIn('build-arg:VITE_APP=demo', args)))

    def test_vue_cli_or_react_without_vite(self):
        self.pkg({'react-scripts':'5'})
        self.run_build(document('npm_static','build',variables={'REACT_APP_SITE':'demo'}), lambda src,args,context: (
            self.assertIn('/app/build/',src),
            self.assertIn('build-arg:REACT_APP_SITE=demo',args)))

    def test_cra_zero_version_is_rejected_before_buildkit(self):
        self.pkg({'react-scripts': '^0.0.0'}, scripts={'build': 'react-scripts build'})
        with self.assertRaisesRegex(ValueError, 'react-scripts.*0.0.0'):
            validate_profile_source(self.repo, 'npm_static', {})

    def test_next_export_requires_out(self):
        self.pkg({'next':'16','react':'19'})
        self.run_build(document('next_export','out',variables={'NEXT_PUBLIC_APP':'demo'}), lambda src,args,context: (
            self.assertIn('/app/out/',src),
            self.assertIn('build-arg:NEXT_PUBLIC_APP=demo',args)))
        with self.assertRaises(ValueError): recipe('next_export',{'output_directory':'dist'}, {})

    def test_next_server_unprivileged_private_port(self):
        self.pkg({'next':'16','react':'19'}, {'build':'next build','start':'next start'})
        self.run_build(document('next_server','.next','AUTO',variables={'NEXT_PUBLIC_API':'https://example.org'}), lambda src,args,context: (
            self.assertIn('USER 10001:10001',src),
            self.assertIn('PORT=8080',src),
            self.assertIn('node .next/standalone/server.js',src),
            self.assertIn('next start -p 8080',src)))

    def test_cannot_inject_shell_or_escape_paths(self):
        for command in ('npm run build; curl evil','sh -c whoami','npm install', 'npm run build && true'):
            with self.assertRaises(ValueError): script_name(command)
        for output in ('../../root','/etc','dist/../etc','../../..', 'dist\\root'):
            with self.assertRaises(ValueError): safe_output(output)
        with self.assertRaises(ValueError): public_build_args({'public_build_variables':{'NEXT_PUBLIC_API_TOKEN':'secret'}},'next_server')
        with self.assertRaises(ValueError): public_build_args({'public_build_variables':{'VITE_API':'x'}},'next_server')
        (self.repo/'index.html').write_text('valid')
        with self.assertRaises(ValueError):validate_profile_source(self.repo,'wrong',{})

    def test_source_checked_not_only_manifest_profile(self):
        self.pkg({'vite':'*'})
        with self.assertRaises(ValueError):validate_profile_source(self.repo,'next_server',{'build_command':'','start_command':'npm run start'})

if __name__=='__main__':unittest.main()
