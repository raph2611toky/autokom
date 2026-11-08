"""SQLite guard in actual checked-out source: standalone, no Django dependency."""
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform' / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts' / 'runtime'))
from apps.projects.git.sqlite_policy import detect_text, sqlite_storage_warning, detect_sqlite_source
from autokom_backend_recipes import BACKEND_RUNTIME, backend_recipe


def cfg(profile):
    return {'runtime': BACKEND_RUNTIME[profile], 'port': 8080, 'build_command':'',
            'start_command':'','output_directory':''}


def source(tmp_path, data):
    for name, content in data.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


@pytest.mark.parametrize('profile, files', [
    ('python_flask', {'requirements.txt':'Flask==3.1.0', 'app.py': 'import sqlite3\nDATABASE = "jirama.db"\napp=None'}),
    ('python_fastapi', {'requirements.txt':'fastapi==0.120', 'main.py':'from sqlite3 import connect\napp=None'}),
    ('python_django', {'requirements.txt':'Django==5.2', 'manage.py':'#placeholder', 'site/wsgi.py':'application=None',
                       'site/settings.py':'DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3"}}'}),
    ('python_wsgi', {'requirements.txt':'gunicorn==23', 'wsgi.py':'engine = "sqlite+pysqlite:///dev.db"'}),
    ('node_express', {'package.json': json.dumps({'dependencies':{'express':'1','better-sqlite3':'1'}, 'scripts':{'start':'node index.js'}}), 'package-lock.json':'{}'}),
    ('node_nest', {'package.json':json.dumps({'dependencies':{'@nestjs/core':'1','sqlite3':'1'}, 'scripts':{'build':'nest build','start:prod':'node dist/main.js'}}),'package-lock.json':'{}'}),
    ('java_spring_maven', {'pom.xml':'<project>spring-boot</project>', 'src/main/resources/application.properties':'spring.datasource.url=jdbc:sqlite:db.sqlite'}),
    ('java_spring_gradle', {'build.gradle':'plugins { id "org.springframework.boot" }', 'src/main/resources/application.yml':'spring: sqlite:///db.sqlite'}),
    ('php_plain', {'index.php': '<?php $db = new SQLite3("foo.db");'}),
    ('php_laravel', {'composer.json':json.dumps({'require':{'laravel/framework':'*'}}), 'composer.lock':'{}','artisan':'<?php','public/index.php':'<?php','config/database.php':"$driver = env('DB_CONNECTION', 'mysql');", '.env':'DB_CONNECTION=sqlite'}),
    ('php_symfony', {'composer.json':json.dumps({'require':{'symfony/framework-bundle':'*'}}), 'composer.lock':'{}','public/index.php':'<?php','config/packages/doctrine.yaml':'DATABASE_URL=sqlite:///tmp/app.db'}),
])
def test_sqlite_backend_profiles_warn_without_blocking(tmp_path, profile, files):
    source(tmp_path, files)
    note = sqlite_storage_warning(tmp_path)
    assert 'SQLite détectée' in note
    # A syntactically valid project with SQLite must pass the storage gate.
    # Recipe compatibility (lockfiles, entrypoints) is tested separately.


def test_genuine_flask_without_sqlite_keeps_working(tmp_path):
    source(tmp_path, {'requirements.txt':'Flask==3.1.0', 'app.py':'from flask import Flask\napp = Flask(__name__)'})
    recipe, _ = backend_recipe('python_flask', tmp_path, cfg('python_flask'))
    assert 'gunicorn' in recipe


def test_sqlite_file_detected_but_documentation_not_a_false_positive(tmp_path):
    source(tmp_path, {'README.md':'import sqlite3\nDB_CONNECTION=sqlite', 'main.py':'print("ok")'})
    assert detect_sqlite_source(tmp_path) is None
    (tmp_path / 'data.sqlite3').write_bytes(b'SQLite format 3\x00')
    assert 'SQLite détectée' in sqlite_storage_warning(tmp_path)


def test_sqlite_db_header_detected_and_normal_db_file_not_blocked(tmp_path):
    (tmp_path / 'arbitrary.db').write_bytes(b'not sqlite')
    assert detect_sqlite_source(tmp_path) is None
    (tmp_path / 'arbitrary.db').write_bytes(b'SQLite format 3\x00' + b'0' * 16)
    assert detect_sqlite_source(tmp_path)[0] == 'arbitrary.db'


def test_symlinks_are_not_followed(tmp_path):
    foreign = tmp_path.parent / 'sqlite-target'
    foreign.write_text('import sqlite3')
    (tmp_path / 'app.py').symlink_to(foreign)
    assert detect_sqlite_source(tmp_path) is None


def test_jirama_app_signature():
    sample = '''from flask import Flask\nimport sqlite3\nDATABASE = 'jirama.db'\ndef get_db(): return sqlite3.connect(DATABASE)'''
    assert detect_text('app.py', sample) == 'Import Python sqlite3'


def test_runner_checks_sources_before_buildkit():
    runner=(ROOT/'scripts/runtime/autokom_agent_local.py').read_text()
    assert runner.index('sqlite_storage_warning(path)') < runner.index('rid=doc[\'run_id\']', runner.index('def build(doc)'))
