"""Bounded, client-safe postmortem for a failed AUTOKOM container startup.

Only the privileged broker can capture Docker state. Never export Docker inspect
Env, mounts, process arguments, raw stdout, or a complete customer traceback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

_SECRET = re.compile(
    r'(?i)(?:password|passwd|secret|token|authorization|bearer|cookie|credentials|'
    r'api[_-]?key|private[_-]?key|github_pat_|gh[pousr]_|-----BEGIN|'
    r'\b[A-Z_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=|https?://\S+@)'
)
_OPAQUE = re.compile(r'(?<![\w])(?:[A-Za-z0-9_-]{48,}|[a-f0-9]{32,})(?![\w])', re.I)
# Fixed, safe diagnostic labels supplied by the trusted broker only.
_REASONS = frozenset({'network_attach_pending', 'network_id_pending', 'ipv4_pending',
                      'inspect_format_invalid', 'inspect_json_invalid',
                      'unexpected_networks', 'network_endpoint_invalid',
                      'network_id_mismatch', 'ipv4_invalid',
                      'ipv6_not_supported', 'ipv4_not_private',
                      'ipv4_outside_subnet', 'healthcheck_no_http'})
# Positive list of *diagnostic formats*, not a generic Docker-log pass-through.
_ALLOWED = (
    re.compile(r'^(?:ModuleNotFoundError|ImportError|SyntaxError|NameError|TypeError|AttributeError|RuntimeError|ValueError|OSError|FileNotFoundError|PermissionError): .{1,220}$'),
    re.compile(r'^(?:sqlite3\.)?OperationalError: (?:no such table|unable to open database file|attempt to write a readonly database|database is locked).{0,120}$', re.I),
    re.compile(r'^(?:django\.db\.utils\.)?(?:OperationalError|ProgrammingError): .{1,160}$'),
    re.compile(r'^\[\d{4}-\d{2}-\d{2} [^\]]+\] \[\d+\] \[(?:ERROR|CRITICAL|INFO)\] (?:Worker failed to boot\.|Booting worker with pid: \d+|Listening at: .{1,90}|Starting gunicorn .{1,80}|Reason: Worker failed to boot\.)$'),
    re.compile(r'^(?:ERROR|Error) loading (?:ASGI|WSGI) app\..{0,180}$'),
    re.compile(r'^(?:Application startup failed\.|Worker failed to boot\.|Traceback \(most recent call last\):|gunicorn\.errors\.HaltServer: .{1,150})$'),
    re.compile(r'^\s*File "(/app/[\w./-]{1,130})", line \d+, in [A-Za-z_][A-Za-z0-9_]{0,60}$'),
    # Nginx: only fixed, non-secret permission errors on standard writable paths.
    re.compile(r'^nginx: \[(?:emerg|alert|crit)\] (?:mkdir|open)\(\) '
               r'"/(?:tmp|var/cache/nginx|run)/[A-Za-z0-9/_\.-]{1,100}" '
               r'failed \(\d+: (?:Permission denied|Read-only file system)\)$'),
    re.compile(r"^Error: (?:EACCES|EROFS): (?:permission denied|read-only file system), "
               r"(?:open|mkdir|scandir) '/app/[A-Za-z0-9/_\.-]{1,100}'$"),
)


def safe_log_line(line):
    """Return allowlisted startup errors only; never an arbitrary application line."""
    if not isinstance(line, str):
        return None
    value = line.replace('\r', '').strip()[:350]
    # Strip docker --timestamps ISO prefix without exporting container IDs.
    value = re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+', '', value)
    if not value or len(value) > 260 or any(ord(c) < 32 for c in value):
        return None
    if _SECRET.search(value) or _OPAQUE.search(value):
        return None
    if not any(p.fullmatch(value) for p in _ALLOWED):
        return None
    return value


def _read_docker(container, command, timeout=8):
    try:
        result = subprocess.run(['docker', *command, container], capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return ''
        # Gunicorn, Python tracebacks and most startup errors are emitted on stderr.
        return (result.stdout + '\n' + result.stderr) if command and command[0] == 'logs' else result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


def capture(root, environment, run_id, container, *, failure='healthcheck', reason=None):
    """Capture before rollback removes the failed container; no raw logs persist."""
    root = Path(root)
    result = {'environment_id': environment, 'run_id': run_id,
              'phase': 'runtime.deploy', 'category': failure, 'container_status': 'unavailable',
              'exit_code': None, 'oom_killed': False, 'restart_count': 0, 'lines': []}
    if reason in _REASONS:
        result['reason'] = reason
    raw = _read_docker(container, ['inspect', '--format', '{{json .State}}'])
    if raw:
        try:
            state = json.loads(raw)
            status = state.get('Status')
            result['container_status'] = status if status in ('created','running','restarting','exited','dead','paused') else 'unknown'
            exit_code = state.get('ExitCode')
            result['exit_code'] = exit_code if type(exit_code) is int and 0 <= exit_code <= 255 else None
            result['oom_killed'] = state.get('OOMKilled') is True
        except (TypeError, ValueError, AttributeError):
            pass
    raw_logs = _read_docker(container, ['logs', '--tail', '90', '--timestamps'], timeout=12)
    result['lines'] = [line for line in (safe_log_line(x) for x in raw_logs[-28000:].splitlines()) if line][-24:]
    directory = root / 'diagnostics'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        return result
    path = directory / (run_id + '.json')
    # Atomic, private, size-bounded snapshot; no bearer tokens or raw output.
    with tempfile.NamedTemporaryFile('w', dir=directory, prefix='.diag-', delete=False, encoding='utf-8') as fp:
        os.fchmod(fp.fileno(), 0o600)
        json.dump(result, fp, ensure_ascii=False)
        temp = fp.name
    os.replace(temp, path)
    return result


def read(root, environment, run_id):
    path = Path(root) / 'diagnostics' / (run_id + '.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20000:
        return {'available': False}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {'available': False}
    if data.get('environment_id') != environment or data.get('run_id') != run_id:
        return {'available': False}
    return {'available': True, 'phase': data.get('phase','runtime.deploy'),
            'category': data.get('category','unknown'),
            'container_status': data.get('container_status','unknown'),
            'reason': data.get('reason') if data.get('reason') in _REASONS else None,
            'exit_code': data.get('exit_code'), 'oom_killed': data.get('oom_killed') is True,
            'lines': [x for x in data.get('lines', [])[:24] if isinstance(x,str) and safe_log_line(x) == x]}
