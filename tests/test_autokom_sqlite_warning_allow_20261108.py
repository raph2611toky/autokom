"""SQLite allowed with an advisory, while backend profile requirements remain strict."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform' / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts' / 'runtime'))
from apps.projects.git.sqlite_policy import detect_text, sqlite_storage_warning, detect_sqlite_source, POLICY_MESSAGE
from autokom_backend_recipes import backend_recipe, BACKEND_RUNTIME


def config(profile):
    return {'runtime': BACKEND_RUNTIME[profile], 'port': 8080, 'build_command': '',
            'start_command': '', 'output_directory': ''}


def test_jirama_like_flask_source_warns_and_build_recipe_is_allowed(tmp_path):
    (tmp_path / 'requirements.txt').write_text('Flask==3.1.0\ngunicorn==23.0.0\n')
    (tmp_path / 'app.py').write_text('from flask import Flask\nimport sqlite3\napp = Flask(__name__)\nDATABASE = "jirama.db"\n')
    assert detect_sqlite_source(tmp_path) == ('app.py', 'Import Python sqlite3')
    assert 'données' in sqlite_storage_warning(tmp_path)
    dockerfile, _ = backend_recipe('python_flask', tmp_path, config('python_flask'))
    assert 'gunicorn' in dockerfile


def test_node_backend_with_sqlite_dependency_not_rejected(tmp_path):
    (tmp_path / 'package.json').write_text(json.dumps({'dependencies': {'express': '5', 'better-sqlite3': '12'}, 'scripts': {'start': 'node index.js'}}))
    (tmp_path / 'package-lock.json').write_text('{}')
    (tmp_path / 'index.js').write_text('const app = require("express")()')
    assert 'SQLite' in sqlite_storage_warning(tmp_path)
    recipe, _ = backend_recipe('node_express', tmp_path, config('node_express'))
    assert 'npm run start' in recipe or '"start"' in recipe


def test_no_sqlite_no_warning(tmp_path):
    (tmp_path / 'app.py').write_text('from flask import Flask\napp=Flask(__name__)')
    assert sqlite_storage_warning(tmp_path) == ''
    assert detect_text('app.py', 'import sqlite3')


def test_client_preview_is_advisory_and_runner_still_logs_notice():
    preview = (ROOT / 'platform/backend/apps/projects/git/views.py').read_text()
    runner = (ROOT / 'scripts/runtime/autokom_agent_local.py').read_text()
    assert 'detected=True' in preview
    assert "'blocked': False" in preview
    assert 'AVERTISSEMENT_STOCKAGE_SQLITE' in runner
    assert 'refuse_sqlite_source(path)' not in runner
    assert 'SQLite détectée' in POLICY_MESSAGE
