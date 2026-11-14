#!/usr/bin/env python3
"""AUTOKOM worker desired-state reconciliation. No inbound management socket.

Read the private GCS manifest, pull immutable images, and route applications
through the worker's nginx. Customer containers remain on internal-only Docker
bridges; the HOST accesses the container IP directly instead of publishing a
loopback port (which Docker may silently omit for --internal bridges).
"""
from __future__ import annotations

from hashlib import sha256
import ipaddress
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
HTTP_LOG_DIR = Path('/var/log/nginx')
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
            return None
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
        # Legacy bind_port is retained in the GCS schema for compatibility,
        # but is NOT published on the host or used to address the container.
        if type(binding) is not int or not 21000 <= binding <= 55000 or binding in ports:
            raise WorkerError('Invalid or conflicting bind port')
        if type(internal) is not int or not 1024 <= internal <= 65535:
            raise WorkerError('Invalid container port')
        if not isinstance(health, str) or not health.startswith('/') or len(health) > 120 or any(x in health for x in ('?', '#', '%', '\\', '\r', '\n', ' ', '://')):
            raise WorkerError('Invalid health path')
        if type(memory) is not int or not 128 <= memory <= 2800 or type(cpu) is not int or not 100 <= cpu <= 1600:
            raise WorkerError('Invalid resource limits')
        envs.add(env); ports.add(binding); hosts.update(hostnames)
        out.append({'environment_id': env, 'run_id': run, 'image': image, 'hosts': hostnames,
                    'bind_port': binding, 'internal_port': internal, 'healthcheck': health,
                    'memory_mb': memory, 'cpu_millicores': cpu})
    # Budget reserve 1024 MiB and 400m CPU to Ubuntu, Docker, nginx, agents.
    if sum(a['memory_mb'] for a in out) > 3072 or sum(a['cpu_millicores'] for a in out) > 1600:
        raise WorkerError('Capacity exceeded: 2 vCPU / 4 GiB worker budget')
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


def container_ipv4(app):
    """Read only the IP on this application's dedicated internal Docker bridge.

    The IP comes from Docker, not from a GCS manifest or user input. Never
    inspect a generic 'first available' network, which could be public.
    """
    result = docker('container', 'inspect', container_name(app), timeout=10)
    try:
        obj = json.loads(result.stdout)
        row = obj[0]['NetworkSettings']['Networks'][network_name(app)]
        address = row['IPAddress']
        parsed = ipaddress.IPv4Address(address)
        if not parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_unspecified:
            raise ValueError('Unexpected Docker IP')
        return str(parsed)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise WorkerError('Missing private Docker bridge IP for ' + app['environment_id']) from exc


def pull(image):
    with tempfile.TemporaryDirectory(prefix='ak-docker-', dir='/run/autokom-ha') as conf:
        os.chmod(conf, 0o700)
        registry = image.split('/', 1)[0]
        credential = token()
        docker('login', '-u', 'oauth2accesstoken', '--password-stdin', 'https://' + registry,
               config=conf, stdin=credential + '\n', timeout=30)
        docker('pull', image, config=conf, timeout=900)


def health(app):
    try:
        address = container_ipv4(app)
    except WorkerError:
        return False
    url = f"http://{address}:{app['internal_port']}{app['healthcheck']}"
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
    if is_running(name, app['image']):
        if health(app):
            return
        # Keep a running but unhealthy candidate for inspection. The old code
        # killed/re-pulled it every ~2 minutes, hiding the real error.
        raise WorkerError('Running app unhealthy; container preserved: ' + app['environment_id'])
    exists = docker('container', 'inspect', name, check=False, timeout=10)
    if exists.returncode == 0:
        docker('rm', '-f', name, timeout=30)
    pull(app['image'])
    network = network_name(app)
    if docker('network', 'inspect', network, check=False, timeout=10).returncode:
        docker('network', 'create', '--internal', '--driver', 'bridge', network, timeout=30)
    # The --internal bridge blocks Docker port publishing on the installed
    # engine. Instead, reach its container address via the local bridge only.
    docker('run', '-d', '--name', name, '--network', network,
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
    lines = ['# AUTOKOM HA - generated only from validated private GCS desired state.',
             'map $http_x_autokom_bench $autokom_ha_bench {',
             '    default "";',
             '    "~^akbench-[a-z0-9-]{1,40}$" $http_x_autokom_bench;',
             '}',
             """log_format autokom_ha_request escape=json '{"time":"$time_iso8601","method":"$request_method","status":"$status","seconds":"$request_time","bytes":"$body_bytes_sent","rx":"$request_length","tx":"$bytes_sent","tag":"$autokom_ha_bench"}';"""]
    for app in apps:
        address = container_ipv4(app)
        for host in app['hosts']:
            log_id = sha256(host.encode('ascii')).hexdigest()
            lines += ['server {', '    listen 8080;', '    server_name ' + host + ';',
                      f'    access_log {HTTP_LOG_DIR}/autokom-ha-{log_id}.log autokom_ha_request;',
                      '    client_max_body_size 25m;',
                      # Keep the private health route authoritative even when
                      # the request Host selects this application's vhost.
                      '    location ^~ /__autokom_health/ { access_log off; proxy_pass http://127.0.0.1:18080; }',
                      '    location / {',
                      f"        proxy_pass http://{address}:{app['internal_port']};",
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
    new_routes = {
        row['environment_id']: {
            'port': row['bind_port'],  # schema compatibility, no host publish
            'container_ip': container_ipv4(row),
            'container_port': row['internal_port'],
            'healthcheck': row['healthcheck'],
            'host': row['hosts'][0],
            'run_id': row['run_id'],
        }
        for row in apps
    }
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
    old_names = previous.get('containers', []) if isinstance(previous, dict) else []
    new_names = [container_name(a) for a in apps]
    for name in old_names:
        if isinstance(name, str) and re.fullmatch(r'ak-[a-f0-9]{12}-[a-f0-9]{12}', name) and name not in new_names:
            # Routes are removed before deleting the old workload. Only networks
            # belonging to environments absent from the new desired state are
            # garbage-collected; updated releases may reuse their bridge.
            result = docker('rm', '-f', name, timeout=30, check=False)
            if result.returncode and 'no such container' not in result.stderr.lower():
                raise WorkerError('Retired container removal failed; retry scheduled')
    old_routes = previous.get('routes', {}) if isinstance(previous, dict) else {}
    if isinstance(old_routes, dict):
        for env in old_routes:
            if (isinstance(env, str) and UUID.fullmatch(env) and
                    env not in new_routes):
                network = 'aknet-' + env.replace('-', '')[:16]
                result = docker('network', 'rm', network, timeout=20, check=False)
                if result.returncode and 'not found' not in result.stderr.lower():
                    raise WorkerError('Retired network removal failed; retry scheduled')
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
                if fingerprint != previous or any(not is_running(container_name(a), a['image']) or not health(a) for a in apps):
                    count = reconcile(cfg, apps)
                    print(f'AUTOKOM_HA_APPS_READY count={count}', flush=True)
                    previous = fingerprint
        except (WorkerError, OSError, ValueError, TypeError, subprocess.TimeoutExpired, urllib.error.URLError) as exc:
            print('AUTOKOM_HA_WORKER_RECONCILE_ERROR reason=' + type(exc).__name__ + ' detail=' + str(exc)[:180], flush=True)
        time.sleep(35)


if __name__ == '__main__':
    loop()
