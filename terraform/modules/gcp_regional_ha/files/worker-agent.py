#!/usr/bin/env python3
"""AUTOKOM worker desired-state reconciliation. No inbound management socket.

The worker reads *only* its own manifest from a private GCS bucket, pulls
immutable digests using a short-lived workload token, and runs bounded Docker
containers behind the local nginx ingress.  It never obtains controller IAM.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG = Path('/etc/autokom-ha/worker.json')
OBSERVED = Path('/var/lib/autokom-ha/observed.json')
ROUTES = Path('/etc/autokom-ha/routed.json')
NGINX = Path('/etc/nginx/conf.d/autokom-ha-apps.conf')
METADATA = 'http://169.254.169.254/computeMetadata/v1/'
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z')
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')


class WorkerError(RuntimeError):
    pass


def metadata(name, as_json=False):
    request = urllib.request.Request(METADATA + name, headers={'Metadata-Flavor': 'Google'})
    with urllib.request.urlopen(request, timeout=8) as result:
        raw = result.read(32768)
    return json.loads(raw) if as_json else raw.decode().strip()


def token():
    value = metadata('instance/service-accounts/default/token', True)
    value = value.get('access_token') if isinstance(value, dict) else None
    if not isinstance(value, str) or len(value) < 24:
        raise WorkerError('GCE credential unavailable')
    return value


def read_manifest(bucket, name, credential):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,220}', bucket):
        raise WorkerError('Invalid GCS bucket')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', name):
        raise WorkerError('Invalid instance name')
    obj = urllib.parse.quote('desired/' + name + '.json', safe='')
    url = f'https://storage.googleapis.com/storage/v1/b/{bucket}/o/{obj}?alt=media'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + credential})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(262145)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # Newly created MIG VM, no assignment yet.
        raise WorkerError('GCS read HTTP_' + str(exc.code)) from None
    if len(raw) > 262144:
        raise WorkerError('Manifest size limit exceeded')
    return json.loads(raw)


def validate(data, cfg, instance, zone):
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('instance') != instance or data.get('zone') != zone:
        raise WorkerError('Manifest identity or schema mismatch')
    apps = data.get('applications')
    if not isinstance(apps, list) or len(apps) > 18:
        raise WorkerError('Manifest size invalid')
    out, hosts, ports, envs = [], set(), set(), set()
    expected = (cfg['region'] + '-docker.pkg.dev/' + cfg['project_id'] + '/' + cfg['artifact_repository'] + '/')
    for row in apps:
        if not isinstance(row, dict):
            raise WorkerError('Invalid application')
        env, run = row.get('environment_id'), row.get('run_id')
        image = row.get('image')
        hostnames = row.get('hosts')
        binding = row.get('bind_port')
        internal = row.get('internal_port')
        health = row.get('healthcheck')
        memory = row.get('memory_mb')
        cpu = row.get('cpu_millicores')
        if not isinstance(env, str) or not UUID.fullmatch(env) or env in envs:
            raise WorkerError('Invalid or duplicate environment ID')
        if not isinstance(run, str) or not UUID.fullmatch(run):
            raise WorkerError('Invalid run ID')
        if not isinstance(image, str) or not image.startswith(expected) or '@' not in image or not DIGEST.fullmatch(image.rsplit('@', 1)[-1]):
            raise WorkerError('Untrusted container reference')
        if not isinstance(hostnames, list) or not (1 <= len(hostnames) <= 8) or any(not isinstance(h, str) or not HOST.fullmatch(h) or h in hosts for h in hostnames):
            raise WorkerError('Invalid or conflicting hostname')
        if type(binding) is not int or not 21000 <= binding <= 55000 or binding in ports:
            raise WorkerError('Invalid or conflicting bind port')
        if type(internal) is not int or not 1024 <= internal <= 65535:
            raise WorkerError('Invalid container port')
        if not isinstance(health, str) or not health.startswith('/') or len(health) > 120 or any(x in health for x in ('?', '#', '%', '\\', '\r', '\n', ' ', '://')):
            raise WorkerError('Invalid health path')
        if type(memory) is not int or not 128 <= memory <= 1400 or type(cpu) is not int or not 100 <= cpu <= 1800:
            raise WorkerError('Invalid resource limits')
        envs.add(env); ports.add(binding); hosts.update(hostnames)
        out.append({'environment_id': env, 'run_id': run, 'image': image, 'hosts': hostnames,
                    'bind_port': binding, 'internal_port': internal, 'healthcheck': health,
                    'memory_mb': memory, 'cpu_millicores': cpu})
    if sum(a['memory_mb'] for a in out) > 1500 or sum(a['cpu_millicores'] for a in out) > 2000:
        raise WorkerError('Capacity exceeded: e2-small worker is overloaded')
    return out


def docker(*args, timeout=90, config=None, stdin=None, check=True):
    argv = ['/usr/bin/docker'] + (['--config', config] if config else []) + list(args)
    proc = subprocess.run(argv, input=stdin, text=True, capture_output=True, timeout=timeout,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'})
    if check and proc.returncode:
        raise WorkerError('Docker command failed: ' + str(args[0]))
    return proc


def container_name(app):
    return 'ak-' + app['environment_id'].replace('-', '')[:12] + '-' + app['run_id'].replace('-', '')[:12]


def network_name(app):
    return 'aknet-' + app['environment_id'].replace('-', '')[:16]


def is_running(name, image):
    result = docker('container', 'inspect', name, '--format', '{{.State.Running}}|{{.Config.Image}}', check=False, timeout=10)
    return result.returncode == 0 and result.stdout.strip() == 'true|' + image


def pull(image):
    with tempfile.TemporaryDirectory(prefix='ak-docker-', dir='/run/autokom-ha') as conf:
        os.chmod(conf, 0o700)
        registry = image.split('/', 1)[0]
        credential = token()
        docker('login', '-u', 'oauth2accesstoken', '--password-stdin', 'https://' + registry,
               config=conf, stdin=credential + '\n', timeout=30)
        docker('pull', image, config=conf, timeout=900)


def health(app):
    url = f"http://127.0.0.1:{app['bind_port']}{app['healthcheck']}"
    req = urllib.request.Request(url, headers={'Host': app['hosts'][0]})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(req, timeout=3) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:
        return 300 <= exc.code < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def install_app(app):
    name = container_name(app)
    if is_running(name, app['image']) and health(app):
        return
    exists = docker('container', 'inspect', name, check=False, timeout=10)
    if exists.returncode == 0:
        # If an identically named container is unhealthy, restart only that owned candidate.
        docker('rm', '-f', name, timeout=30)
    pull(app['image'])
    network = network_name(app)
    if docker('network', 'inspect', network, check=False, timeout=10).returncode:
        docker('network', 'create', '--internal', '--driver', 'bridge', network, timeout=30)
    docker('run', '-d', '--name', name, '--network', network,
           '--publish', f"127.0.0.1:{app['bind_port']}:{app['internal_port']}",
           '--cpus', str(app['cpu_millicores'] / 1000), '--memory', str(app['memory_mb']) + 'm',
           '--memory-swap', str(app['memory_mb']) + 'm', '--pids-limit', '128',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
           '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m', '--user', '10001:10001',
           '--restart', 'unless-stopped', app['image'], timeout=90)
    for _ in range(45):
        if health(app):
            return
        time.sleep(2)
    raise WorkerError('App did not become healthy: ' + app['environment_id'])


def _atomic_write(dest, content, mode=0o644):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.ak-', dir=dest.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            os.fchmod(file.fileno(), mode)
            file.write(content)
            file.flush(); os.fsync(file.fileno())
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def render_nginx(apps):
    lines = ['# AUTOKOM HA - generated only from validated private GCS desired state.']
    for app in apps:
        for host in app['hosts']:
            lines += ['server {', '    listen 8080;', '    server_name ' + host + ';',
                      '    client_max_body_size 25m;', '    location / {',
                      f"        proxy_pass http://127.0.0.1:{app['bind_port']};",
                      '        proxy_http_version 1.1;', '        proxy_set_header Host $host;',
                      '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
                      '        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;',
                      '        proxy_set_header Upgrade $http_upgrade;',
                      '        proxy_set_header Connection "upgrade";',
                      '    }', '}']
    return '\n'.join(lines) + '\n'


def reconcile(cfg, apps):
    previous = {}
    if OBSERVED.is_file() and not OBSERVED.is_symlink():
        try:
            previous = json.loads(OBSERVED.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            previous = {}
    for app in apps:
        install_app(app)
    new_routes = {row['environment_id']: {'port': row['bind_port'], 'healthcheck': row['healthcheck'], 'host': row['hosts'][0], 'run_id': row['run_id']} for row in apps}
    rendered = render_nginx(apps)
    original = NGINX.read_text() if NGINX.is_file() else ''
    _atomic_write(NGINX, rendered)
    tested = subprocess.run(['/usr/sbin/nginx', '-t'], capture_output=True, timeout=12)
    if tested.returncode:
        _atomic_write(NGINX, original)
        raise WorkerError('Nginx configuration rejected; previous routes restored')
    _atomic_write(ROUTES, json.dumps(new_routes, sort_keys=True) + '\n')
    if rendered != original:
        result = subprocess.run(['/usr/bin/systemctl', 'reload', 'nginx.service'], capture_output=True, timeout=20)
        if result.returncode:
            _atomic_write(NGINX, original)
            _atomic_write(ROUTES, json.dumps(previous.get('routes', {}), sort_keys=True) + '\n')
            raise WorkerError('Nginx reload failed; old configuration restored')
    # Only prune containers once the new ingress was accepted; no other Docker images touched.
    old_names = previous.get('containers', []) if isinstance(previous, dict) else []
    new_names = [container_name(a) for a in apps]
    for name in old_names:
        if isinstance(name, str) and re.fullmatch(r'ak-[a-f0-9]{12}-[a-f0-9]{12}', name) and name not in new_names:
            docker('rm', '-f', name, timeout=30, check=False)
    _atomic_write(OBSERVED, json.dumps({'containers': new_names, 'routes': new_routes}, sort_keys=True) + '\n', 0o600)
    return len(apps)


def loop():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    instance = metadata('instance/name')
    zone = metadata('instance/zone').rsplit('/', 1)[-1]
    bucket = cfg.get('state_bucket')
    if not isinstance(bucket, str) or not bucket:
        raise WorkerError('State bucket is not provisioned')
    previous = None
    while True:
        try:
            content = read_manifest(bucket, instance, token())
            if content is not None:
                apps = validate(content, cfg, instance, zone)
                fingerprint = json.dumps(apps, sort_keys=True)
                # Recheck state even with unchanged manifest (containers may have crashed).
                if fingerprint != previous or any(not is_running(container_name(a), a['image']) or not health(a) for a in apps):
                    count = reconcile(cfg, apps)
                    print(f'AUTOKOM_HA_APPS_READY count={count}', flush=True)
                    previous = fingerprint
        except (WorkerError, OSError, ValueError, TypeError, subprocess.TimeoutExpired, urllib.error.URLError) as exc:
            print('AUTOKOM_HA_WORKER_RECONCILE_ERROR reason=' + type(exc).__name__, flush=True)
        time.sleep(35)


if __name__ == '__main__':
    loop()
