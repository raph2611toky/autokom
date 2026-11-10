"""Regression: Nginx UID 10001, managed ports and safe postmortem logs.

No Docker daemon or production service is touched by these tests.
"""
import ast
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/runtime'))
sys.path.insert(0, str(ROOT/'scripts/lib'))
from autokom_frontend_recipes import recipe
from autokom_backend_recipes import backend_recipe
from autokom_internal_ingress import pick_loopback_port
from autokom_customer_diagnostics import safe_log_line


class GeneratedNginxPermissionTests(unittest.TestCase):
    def test_all_nginx_frontends_install_world_readable_config_and_validate_at_build(self):
        for profile, output in [('vite','dist'),('npm_static','build'),('next_export','out'),('html','')]:
            with self.subTest(profile=profile):
                doc={'mode':'DEVOPS'}
                dockerfile,_ = recipe(profile,{'output_directory':output},doc)
                self.assertIn('COPY --chmod=0644 autokom-nginx.conf /etc/nginx/conf.d/default.conf',dockerfile)
                self.assertIn('RUN nginx -t',dockerfile)
                self.assertIn('USER 10001:10001',dockerfile)
                self.assertLess(dockerfile.index('USER 10001:10001'), dockerfile.index('RUN nginx -t'))
                self.assertIn('EXPOSE 8080',dockerfile)

    def test_direct_vite_special_path_applies_same_contract(self):
        script=(ROOT/'scripts/runtime/autokom_agent_local.py').read_text()
        self.assertIn('COPY --chmod=0644 --from=builder /tmp/autokom-nginx.conf',script)
        self.assertIn('RUN nginx -t',script)
        self.assertIn('USER 10001:10001',script)

    def test_nginx_config_permission_error_is_visible_but_secrets_are_not(self):
        line='nginx: [emerg] open() "/etc/nginx/conf.d/default.conf" failed (13: Permission denied)'
        self.assertEqual(safe_log_line(line),line)
        self.assertIsNone(safe_log_line('nginx: [emerg] open() "/etc/shadow" failed (13: Permission denied)'))
        self.assertIsNone(safe_log_line('TOKEN=abc123'))


class ManagedPortTests(unittest.TestCase):
    def test_host_loopback_port_is_automatically_five_digits(self):
        port=pick_loopback_port()
        self.assertGreaterEqual(port,10000)
        self.assertLessEqual(port,65535)

    def test_managed_port_contract_is_used_on_create_and_update(self):
        services=(ROOT/'platform/backend/apps/projects/configurations/services.py').read_text()
        views=(ROOT/'platform/backend/apps/projects/configurations/views.py').read_text()
        ast.parse(services)
        ast.parse(views)
        self.assertIn('def managed_internal_port(', services)
        self.assertIn("cfg.internal_port != 8000", services)
        self.assertIn("data['internal_port'] = port", services)
        self.assertIn('cfg.internal_port = managed_port', views)

    def test_backend_port_helper_actual_behavior(self):
        module=(ROOT/'platform/backend/apps/projects/configurations/services.py').read_text()
        tree=ast.parse(module)
        fn=next(item for item in tree.body if isinstance(item,ast.FunctionDef)
                and item.name=='managed_internal_port')
        ns={'DeploymentConfig':types.SimpleNamespace(BuildMethod=types.SimpleNamespace(AUTO='AUTO',STATIC='STATIC',DOCKERFILE='DOCKERFILE')),
            'BACKEND_RUNTIMES':{'python_fastapi':'PYTHON','node_express':'NODE','php_plain':'PHP'}}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<managed_internal_port>','exec'),ns)
        choose=ns['managed_internal_port']
        for profile in ('python_fastapi','node_express','php_plain'):
            self.assertEqual(choose('AUTO',{'frontend_profile':profile}),8000)
        self.assertEqual(choose('AUTO',{'frontend_profile':'next_server'}),8080)
        self.assertEqual(choose('STATIC',{'frontend_profile':'vite'}),8080)
        self.assertIsNone(choose('DOCKERFILE',{'frontend_profile':'python_fastapi'}))

    def test_php_nginx_and_fpm_config_are_readable_under_runtime_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'index.php').write_text('<?php echo "ok";')
            dockerfile,_=backend_recipe('php_plain',path,{'runtime':'PHP','port':8000,
                        'build_command':'','start_command':'','output_directory':''})
            self.assertIn('COPY --chmod=0644 autokom-php-nginx.conf',dockerfile)
            self.assertIn('COPY --chmod=0644 autokom-php-fpm.conf',dockerfile)
            self.assertIn('USER 10001:10001',dockerfile)

    def test_legacy_backend_manifests_still_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'requirements.txt').write_text('flask==3.1.0')
            (path/'app.py').write_text('from flask import Flask\napp=Flask(__name__)')
            for port in (8000,8080):
                dockerfile,_=backend_recipe('python_flask',path,{'runtime':'PYTHON','port':port,
                                        'build_command':'','start_command':'','output_directory':''})
                self.assertIn(f'EXPOSE {port}',dockerfile)

if __name__=='__main__': unittest.main()
