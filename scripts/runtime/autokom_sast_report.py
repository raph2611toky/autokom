"""Bounded local Semgrep run and DeepSAST advisory summary."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

MAX_REPORT = 8 * 1024 * 1024
SAFE_RULE = re.compile(r'^[A-Za-z0-9_.-]{1,180}$')


def summarize(rows):
    rules = {}
    for item in rows:
        name = item.get('check_id') if isinstance(item, dict) else None
        if isinstance(name, str) and SAFE_RULE.fullmatch(name):
            rules[name] = rules.get(name, 0) + 1
    return dict(sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))[:20])


def collect(path, rules, env, ai_python, ai_model, *, run=subprocess.run, on_inference=lambda: None):
    with tempfile.TemporaryDirectory(prefix='autokom-sast-') as folder:
        output = Path(folder) / 'semgrep.json'
        env = {**env, 'HOME': folder, 'XDG_CACHE_HOME': folder, 'XDG_CONFIG_HOME': folder}
        process = run(['semgrep', 'scan', '--metrics', 'off', '--config', rules,
                       '--disable-version-check', '--no-rewrite-rule-ids', '--json', '--output', str(output),
                       '--error', str(path)], cwd=folder, env=env, timeout=240,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not output.is_file() or output.stat().st_size > MAX_REPORT or process.returncode not in (0, 1):
            raise ValueError('Analyse Semgrep indisponible.')
        document = json.loads(output.read_text(encoding='utf-8'))
    rows = document.get('results')
    if not isinstance(rows, list) or bool(rows) != (process.returncode == 1):
        raise ValueError('Résultat Semgrep incohérent.')
    ai = {'status': 'DISABLED', 'analyzed': 0}
    if ai_python and ai_model:
        on_inference()
        # Only location coordinates pass to the local inference process.
        locations = [{'path': item.get('path'), 'start': {'line': (item.get('start') or {}).get('line')},
                      'end': {'line': (item.get('end') or {}).get('line')}} for item in rows[:24]]
        ai_env = {key: value for key, value in env.items() if key in ('PATH', 'LANG', 'LC_ALL')}
        try:
            result = run([ai_python, '-I', str(Path(__file__).with_name('autokom_deep_sast.py')),
                          str(path), ai_model], input=json.dumps(locations), env=ai_env,
                         cwd='/', text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         timeout=100, check=True)
            ai = json.loads(result.stdout[:4096])
        except (ValueError, OSError, subprocess.SubprocessError):
            ai = {'status': 'UNAVAILABLE', 'analyzed': 0}
    # Do not transmit snippets, location paths, command lines, tool errors, or raw output.
    return {'count': len(rows), 'rules': summarize(rows), 'ai': ai}, process.returncode
