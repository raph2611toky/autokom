#!/usr/bin/env python3
"""Automatic AUTOKOM cloud delivery (private worker GCS manifests + app-specific zonal NEGs).

It validates each replica and automatically routes eligible managed HA domains
after verifying HTTPS against the exact release. The primary remains available.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autokom_ha_registry import metadata_token
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import autokom_ha_dns as ha_dns

PROJECT = 'plucky-stacker-480421-c4'
REGION = 'us-central1'
MIG = 'autokom-ha-rmig'
LB_MAP = 'autokom-ha-map'
NETWORK = 'default'
SUBNET = 'default'
PORT = 8080
APP_MEMORY_BUDGET_MB = 3072
APP_CPU_BUDGET_MILLICORES = 1600
BASELINE_WORKERS = 2
MAX_WORKERS = 5
AUTOSCALER = 'autokom-ha-autoscaler'
SCALE_IN_DELAY_SECONDS = 1800
BUCKET = f'{PROJECT}-autokom-ha-state'
STATE = Path('/var/lib/autokom/client-runtime')
PUBLISHED = Path('/var/lib/autokom/ha/published')
REPORT = Path('/var/lib/autokom/ha/delivery-status.json')
PUBLIC_METRICS = Path('/run/autokom-ha-app-metrics.prom')
LOCK = Path('/run/lock/autokom-ha-delivery.lock')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z')
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
BASE = f'https://compute.googleapis.com/compute/v1/projects/{PROJECT}'


class DeliveryError(RuntimeError):
    pass


def api(method, url, data=None, *, missing_ok=False, timeout=35):
    if not url.startswith(('https://compute.googleapis.com/compute/v1/projects/' + PROJECT + '/',
                           'https://storage.googleapis.com/')):
        raise DeliveryError('Unauthorized API host')
    payload = json.dumps(data).encode('utf-8') if data is not None else None
    headers = {'Authorization': 'Bearer ' + metadata_token(), 'Accept': 'application/json'}
    if payload is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(2097153)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise DeliveryError('GCP API ' + method + ' HTTP_' + str(exc.code)) from None
    if len(raw) > 2097152:
        raise DeliveryError('GCP API reply exceeds size bound')
    return json.loads(raw) if raw else {}


def compute(method, route, payload=None, missing_ok=False):
    if not route.startswith('/') or '..' in route or '?' in route:
        raise DeliveryError('Invalid Compute API route')
    return api(method, BASE + route, payload, missing_ok=missing_ok)


def operation(doc, scope):
    name = doc.get('name') if isinstance(doc, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', name):
        raise DeliveryError('Compute operation ID missing')
    if scope not in ('global', 'regions/us-central1', 'zones/us-central1-a', 'zones/us-central1-f'):
        raise DeliveryError('Unapproved Compute operation scope')
    route = f'/global/operations/{name}' if scope == 'global' else f'/{scope}/operations/{name}'
    for _ in range(65):
        current = compute('GET', route)
        if current.get('status') == 'DONE':
            if current.get('error'):
                raise DeliveryError('Compute operation finished with error')
            return
        time.sleep(2)
    raise DeliveryError('Compute operation timed out')


def gcs_get(name):
    safe = urllib.parse.quote(name, safe='')
    uri = f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{safe}?alt=media'
    return api('GET', uri, missing_ok=True)


def gcs_put(name, payload):
    if not re.fullmatch(r'desired/[a-z][a-z0-9-]{0,62}\.json', name):
        raise DeliveryError('Invalid GCS object')
    if gcs_get(name) == payload:
        return False
    data = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    if len(data) > 262144:
        raise DeliveryError('GCS desired state exceeds bound')
    url = (f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?'
           + urllib.parse.urlencode({'uploadType': 'media', 'name': name}))
    request = urllib.request.Request(url, method='POST', data=data,
       headers={'Authorization': 'Bearer ' + metadata_token(), 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=45) as res:
            if res.status not in (200, 201):
                raise DeliveryError('GCS media upload refused')
    except urllib.error.HTTPError as exc:
        raise DeliveryError('GCS upload HTTP_' + str(exc.code)) from None
    return True


def valid_uuid(s):
    return isinstance(s, str) and bool(UUID.fullmatch(s))


def _private_json(path: Path, max_bytes=16384):
    if path.is_symlink() or not path.is_file():
        return None
    st = path.stat(follow_symlinks=False)
    if st.st_uid != 0 or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o022 or st.st_size > max_bytes:
        return None
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
    return doc if isinstance(doc, dict) else None


def selected_hosts(env):
    hosts = []
    for owner in STATE.glob('domain-owner-*.txt'):
        if owner.is_symlink() or not owner.is_file() or owner.stat().st_uid != 0:
            continue
        host = owner.name[len('domain-owner-'):-len('.txt')]
        if not HOST.fullmatch(host) or owner.stat().st_size > 128:
            continue
        if owner.read_text(encoding='utf-8').strip() == env:
            hosts.append(host)
    return sorted(set(hosts))[:8]


def committed_apps():
    candidates = []
    if not STATE.is_dir():
        return candidates
    for folder in sorted(STATE.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not valid_uuid(folder.name):
            continue
        current = _private_json(folder / 'current.json')
        if not current or not current.get('cloud_delivery_requested') or current.get('environment_id') != folder.name:
            continue
        if (folder / 'previous.json').exists():
            continue
        rid = current.get('run_id')
        if not valid_uuid(rid):
            continue
        if current.get('variables_present') or current.get('stack_project_id'):
            continue  # No plaintext secrets or non-atomic frontend/backend deployments.
        published = _private_json(PUBLISHED / (rid + '.json'))
        if not published or published.get('run_id') != rid:
            continue
        image = published.get('image')
        prefix = f'{REGION}-docker.pkg.dev/{PROJECT}/autokom-ha-images/'
        if not isinstance(image, str) or not image.startswith(prefix) or '@' not in image or not DIGEST.fullmatch(image.rsplit('@', 1)[-1]):
            continue
        internal, health, mem, cpu = (current.get(field) for field in ('internal_port','healthcheck','memory_mb','cpu_millicores'))
        if (type(internal) is not int or not 1024 <= internal <= 65535 or
            not isinstance(health, str) or not health.startswith('/') or len(health) > 120 or any(x in health for x in ('?', '#', '%', '\\', '\r', '\n', ' ', '://')) or
            type(mem) is not int or not 128 <= mem <= 2800 or
            type(cpu) is not int or not 100 <= cpu <= 1600):
            continue
        hosts = selected_hosts(folder.name)
        if not hosts:
            continue
        candidates.append({'environment_id': folder.name, 'run_id': rid, 'image': image,
                           'hosts': hosts, 'internal_port': internal, 'healthcheck': health,
                           'memory_mb': mem, 'cpu_millicores': cpu,
                           'ha': current.get('high_availability_requested') is True})
    return candidates


def running_workers():
    result = compute('POST', f'/regions/{REGION}/instanceGroupManagers/{MIG}/listManagedInstances', {})
    instances = result.get('managedInstances', [])
    if not isinstance(instances, list):
        raise DeliveryError('Managed instance list invalid')
    output = []
    for row in instances:
        if not isinstance(row, dict):
            continue
        name = str(row.get('instance', '')).rsplit('/', 1)[-1]
        zone = str(row.get('instance', '')).split('/zones/')[-1].split('/')[0]
        checks = row.get('instanceHealth') or []
        healthy = any(i.get('detailedHealthState') == 'HEALTHY' for i in checks if isinstance(i, dict))
        if (row.get('instanceStatus') != 'RUNNING' or row.get('currentAction', 'NONE') != 'NONE' or
                zone not in ('us-central1-a','us-central1-f') or not healthy or
                not re.fullmatch(r'autokom-ha-web-[a-z0-9]{1,12}', name)):
            continue
        detail = compute('GET', f'/zones/{zone}/instances/{name}')
        ip = ((detail.get('networkInterfaces') or [{}])[0]).get('networkIP')
        if not isinstance(ip, str) or not re.fullmatch(r'10\.(?:\d{1,3}\.){2}\d{1,3}', ip):
            continue
        # During a rolling template change the old e2-small instances may
        # coexist with the new e2-custom-2-4096 generation. Do not overload
        # a still-running old worker by assuming it already has 4 GiB RAM.
        machine = str(detail.get('machineType') or '').rsplit('/', 1)[-1]
        new_class = machine == 'e2-custom-2-4096'
        output.append({'name': name, 'zone': zone, 'ip': ip, 'memory': 0, 'cpu': 0,
                       'memory_limit': APP_MEMORY_BUDGET_MB if new_class else 1500,
                       'cpu_limit': APP_CPU_BUDGET_MILLICORES if new_class else 2000,
                       'generation': 'custom-2-4096' if new_class else 'legacy'})
    return sorted(output, key=lambda r: (r['zone'], r['name']))


def assign(apps, workers):
    """Standard=1 worker; HA=2 distinct zones; reject requests beyond measured budgets."""
    manifests = {row['name']: [] for row in workers}
    decisions = []
    used_ports = {row['name']: set() for row in workers}
    for app in sorted(apps, key=lambda a: (not a['ha'], a['environment_id'])):
        wanted = 2 if app['ha'] else 1
        choice = []
        for worker in sorted(workers, key=lambda w: (w['memory'], w['cpu'], w['zone'], w['name'])):
            if (worker['memory'] + app['memory_mb'] > worker.get('memory_limit', APP_MEMORY_BUDGET_MB) or
                    worker['cpu'] + app['cpu_millicores'] > worker.get('cpu_limit', APP_CPU_BUDGET_MILLICORES)):
                continue
            if any(existing['zone'] == worker['zone'] for existing in choice):
                continue
            choice.append(worker)
            if len(choice) == wanted:
                break
        if len(choice) != wanted:
            decisions.append({'environment': app['environment_id'], 'status': 'INSUFFICIENT_CAPACITY', 'ha': app['ha']})
            continue
        for worker in choice:
            alloc = dict(app)
            alloc.pop('ha', None)
            # Stable per run+environment; use a small collision fallback.
            port = 21000 + int(hashlib.sha256((app['environment_id'] + app['run_id']).encode()).hexdigest()[:8], 16) % 29000
            while port in used_ports[worker['name']]:
                port = 21000 + (port - 20999) % 29000
            alloc['bind_port'] = port
            used_ports[worker['name']].add(port)
            manifests[worker['name']].append(alloc)
            worker['memory'] += app['memory_mb']; worker['cpu'] += app['cpu_millicores']
        decisions.append({'environment': app['environment_id'], 'status': 'ASSIGNED', 'ha': app['ha'],
                          'workers': [{'name': w['name'], 'zone': w['zone'], 'ip': w['ip']} for w in choice],
                          'run_id': app['run_id'], 'hosts': app['hosts']})
    return manifests, decisions


def minimum_workers_for_demand(apps, max_workers=MAX_WORKERS):
    """Compute a minimum autoscaler floor before placement.

    Assumes GCP regional redistribution evenly balances the two zones;
    actual placement always revalidates the health of distinct VMs. A floor
    raises capacity for *pending applications*, not only CPU load.
    """
    if not apps:
        return BASELINE_WORKERS
    for size in range(BASELINE_WORKERS, max_workers + 1):
        predicted = [
            {'name': f'autokom-ha-web-plan{i}',
             'zone': ('us-central1-a' if i % 2 == 0 else 'us-central1-f'),
             'ip': f'10.0.0.{i+1}', 'memory': 0, 'cpu': 0}
            for i in range(size)
        ]
        _, states = assign(apps, predicted)
        if all(row['status'] == 'ASSIGNED' for row in states):
            return size
    return None


def autoscaler_status():
    doc = compute('GET', f'/regions/{REGION}/autoscalers/{AUTOSCALER}')
    policy = doc.get('autoscalingPolicy') if isinstance(doc, dict) else None
    if not isinstance(policy, dict):
        raise DeliveryError('Autoscaler configuration missing')
    minimum = policy.get('minNumReplicas')
    maximum = policy.get('maxNumReplicas')
    if type(minimum) is not int or type(maximum) is not int or not 2 <= minimum <= maximum:
        raise DeliveryError('Invalid regional autoscaler min/max')
    return minimum, maximum


def desired_floor(apps, previous, *, now=None, autoscaler_min, autoscaler_max):
    """Scale out immediately; scale in only after 30min stable low demand."""
    now = int(time.time() if now is None else now)
    required = minimum_workers_for_demand(apps, min(MAX_WORKERS, autoscaler_max))
    if required is None:
        return {'required': None, 'target': autoscaler_min, 'status': 'CAPACITY_LIMIT',
                'lower_since': 0}
    if required > autoscaler_min:
        return {'required': required, 'target': required, 'status': 'SCALE_OUT_REQUESTED',
                'lower_since': 0}
    if required == autoscaler_min:
        return {'required': required, 'target': autoscaler_min, 'status': 'STABLE',
                'lower_since': 0}
    # Existing HTTP traffic must remain routed while new manifests settle.
    old_capacity = previous.get('capacity') if isinstance(previous, dict) else None
    before = old_capacity if isinstance(old_capacity, dict) else {}
    lower_since = before.get('lower_since', 0)
    if before.get('required') != required or type(lower_since) is not int or lower_since <= 0:
        lower_since = now
    if now - lower_since < SCALE_IN_DELAY_SECONDS:
        return {'required': required, 'target': autoscaler_min, 'status': 'SCALE_IN_STABILIZING',
                'lower_since': lower_since}
    return {'required': required, 'target': required, 'status': 'SCALE_IN_REQUESTED',
            'lower_since': 0}


def apply_capacity_floor(capacity, current_min):
    """Patch only minNumReplicas; preserve GCP's CPU/load policy and max."""
    target = capacity['target']
    if target == current_min:
        return False
    if type(target) is not int or not BASELINE_WORKERS <= target <= MAX_WORKERS:
        raise DeliveryError('Unsafe autoscaler minimum')
    uri = (f'{BASE}/regions/{REGION}/autoscalers?'
           + urllib.parse.urlencode({'autoscaler': AUTOSCALER}))
    result = api('PATCH', uri, {'autoscalingPolicy': {'minNumReplicas': target}})
    operation(result, 'regions/' + REGION)
    return True


def deleted_environments(previous, apps):
    """Only remove routes of a previously owned release after local teardown.

    Project deletion broker removes both current.json and owner markers. Do
    not infer deletion from missing GCS, failed image delivery, or capacity.
    """
    active = {a['environment_id'] for a in apps}
    orphaned = []
    # Retry URL-map removal if the previous cycle failed. Removing a deleted
    # release is idempotent, and no live environment is ever targeted.
    candidates = list(previous.get('applications', [])) if isinstance(previous, dict) else []
    if isinstance(previous, dict):
        candidates += [{'environment': env} for env in previous.get('pending_deletions', [])
                       if isinstance(env, str)]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        env = item.get('environment')
        if not valid_uuid(env) or env in active:
            continue
        directory = STATE / env
        if ((directory / 'current.json').exists() or
                (directory / 'previous.json').exists() or selected_hosts(env)):
            continue
        orphaned.append(env)
    return sorted(set(orphaned))


def unroute_deleted(environments):
    """Remove only AUTOKOM-owned URL map host rules and path matchers.

    Never delete shared load balancer, Terraform resources or another app's
    rule. Unrouted app-specific backends can be garbage-collected separately.
    """
    if not environments:
        return False
    doc = compute('GET', '/global/urlMaps/' + LB_MAP)
    target = {'akp-' + env.replace('-', '')[:20] for env in environments}
    old_rules = list(doc.get('hostRules') or [])
    old_matchers = list(doc.get('pathMatchers') or [])
    rules = [row for row in old_rules if row.get('pathMatcher') not in target]
    matchers = [row for row in old_matchers if row.get('name') not in target]
    if len(rules) == len(old_rules) and len(matchers) == len(old_matchers):
        return False
    operation(compute('PATCH', '/global/urlMaps/' + LB_MAP,
                      {'hostRules': rules, 'pathMatchers': matchers,
                       'fingerprint': doc.get('fingerprint')}), 'global')
    return True


def get_or_create(route, body, collection, scope='global'):
    existing = compute('GET', route, missing_ok=True)
    if existing:
        return existing
    operation(compute('POST', collection, body), scope)
    created = compute('GET', route)
    if not created:
        raise DeliveryError('Compute resource disappeared after creation')
    return created


def name_for(app, kind):
    return 'ak-' + kind + '-' + app['environment_id'].replace('-', '')[:12] + '-' + app['run_id'].replace('-', '')[:12]


def ensure_neg(app, worker):
    name = name_for(app, 'n')
    zone = worker['zone']
    root = f'/zones/{zone}/networkEndpointGroups'
    get_or_create(root + '/' + name,
                  {'name': name, 'networkEndpointType': 'GCE_VM_IP_PORT', 'defaultPort': PORT,
                   'network': f'{BASE}/global/networks/{NETWORK}',
                   'subnetwork': f'{BASE}/regions/{REGION}/subnetworks/{SUBNET}'}, root,
                  scope='zones/' + zone)
    current = compute('POST', root + '/' + name + '/listNetworkEndpoints', {'showHealthStatus': False})
    old = current.get('items') or []
    desired = {'instance': worker['name'], 'ipAddress': worker['ip'], 'port': PORT}
    already = any(isinstance(row, dict) and row.get('networkEndpoint', {}).get('instance', '').rsplit('/', 1)[-1] == worker['name']
                  and row.get('networkEndpoint', {}).get('ipAddress') == worker['ip'] for row in old)
    if not already:
        operation(compute('POST', root + '/' + name + '/attachNetworkEndpoints', {'networkEndpoints': [desired]}), 'zones/' + zone)
    stale = [{'instance': row['networkEndpoint']['instance'], 'ipAddress': row['networkEndpoint'].get('ipAddress'),
              'port': row['networkEndpoint'].get('port', PORT)} for row in old
             if isinstance(row, dict) and isinstance(row.get('networkEndpoint'), dict) and
             row['networkEndpoint'].get('instance', '').rsplit('/', 1)[-1] != worker['name']]
    if stale:
        operation(compute('POST', root + '/' + name + '/detachNetworkEndpoints', {'networkEndpoints': stale}), 'zones/' + zone)
    return f'{BASE}{root}/{name}'


def ensure_backend(app, workers):
    neg_urls = [ensure_neg(app, w) for w in workers]
    hc_name = name_for(app, 'h')
    hc = get_or_create('/global/healthChecks/' + hc_name,
        {'name': hc_name, 'type': 'HTTP', 'checkIntervalSec': 10, 'timeoutSec': 5,
         'healthyThreshold': 2, 'unhealthyThreshold': 3,
         'httpHealthCheck': {'port': PORT, 'requestPath': '/__autokom_health/' + app['environment_id'] + '/' + app['run_id']}},
        '/global/healthChecks')
    backend_name = name_for(app, 'b')
    desired_backends = [{'group': url, 'balancingMode': 'RATE', 'maxRatePerEndpoint': 100} for url in neg_urls]
    doc = get_or_create('/global/backendServices/' + backend_name,
         {'name': backend_name, 'loadBalancingScheme': 'EXTERNAL_MANAGED', 'protocol': 'HTTP',
          'timeoutSec': 30, 'healthChecks': [hc['selfLink']], 'backends': desired_backends},
         '/global/backendServices')
    current = {b.get('group') for b in doc.get('backends', [])}
    target = {b['group'] for b in desired_backends}
    if current != target:
        operation(compute('PATCH', '/global/backendServices/' + backend_name,
                          {'backends': desired_backends, 'fingerprint': doc.get('fingerprint')}), 'global')
    return f'{BASE}/global/backendServices/{backend_name}', desired_backends


def backend_healthy(app, backends, *, expected=None):
    name = name_for(app, 'b')
    healthy = set()
    allowed = {(worker['zone'], worker['name']) for worker in expected} if expected is not None else None
    for backend in backends:
        group = backend['group']
        zone = re.search(r'/zones/([a-z0-9-]+)/networkEndpointGroups/', group)
        if not zone:
            continue
        doc = compute('POST', '/global/backendServices/' + name + '/getHealth', {'group': group})
        for status in doc.get('healthStatus') or []:
            instance = str(status.get('instance', '')).rsplit('/', 1)[-1]
            key = (zone[1], instance)
            if (status.get('healthState') == 'HEALTHY' and instance and
                    (allowed is None or key in allowed)):
                healthy.add(key)
    return len(healthy)


def route_host(app, backend):
    path_name = 'akp-' + app['environment_id'].replace('-', '')[:20]
    doc = compute('GET', '/global/urlMaps/' + LB_MAP)
    rules = list(doc.get('hostRules') or [])
    matchers = list(doc.get('pathMatchers') or [])
    existing = next((r for r in rules if r.get('pathMatcher') == path_name), None)
    for host in app['hosts']:
        for rule in rules:
            if host in (rule.get('hosts') or []) and rule.get('pathMatcher') != path_name:
                raise DeliveryError('Hostname already bound by a different service')
    if existing:
        rules = [r for r in rules if r.get('pathMatcher') != path_name]
    rules.append({'hosts': app['hosts'], 'pathMatcher': path_name})
    matchers = [m for m in matchers if m.get('name') != path_name]
    matchers.append({'name': path_name, 'defaultService': backend})
    if existing and existing.get('hosts') == app['hosts'] and any(m.get('name') == path_name and m.get('defaultService') == backend for m in doc.get('pathMatchers') or []):
        return False
    operation(compute('PATCH', '/global/urlMaps/' + LB_MAP, {'hostRules': rules, 'pathMatchers': matchers,
                                                              'fingerprint': doc.get('fingerprint')}), 'global')
    return True


def _atomic_report(doc, destination=REPORT):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.delivery-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(doc, stream, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _metrics_from_report(report):
    """Only generated, validated identifiers. No images, hosts or credentials."""
    lines = ['# TYPE autokom_ha_app_replicas_requested gauge',
             '# TYPE autokom_ha_app_replicas_healthy gauge',
             '# TYPE autokom_ha_app_http_ready gauge',
             '# TYPE autokom_ha_app_https_ready gauge']
    for item in report.get('applications', []):
        env = item.get('environment')
        if not valid_uuid(env):
            continue
        mode = 'ha' if item.get('ha') is True else 'standard'
        labels = '{environment="' + env + '",mode="' + mode + '"}'
        count = item.get('healthy_replicas', 0)
        count = count if type(count) is int and 0 <= count <= 20 else 0
        wanted = 2 if mode == 'ha' else 1
        http_ready = int(item.get('status') in ('HTTP_LB_READY_NOT_HTTPS', 'PUBLIC_DNS_PROPAGATING', 'PUBLIC_HTTPS_ACTIVE'))
        for key, value in (('autokom_ha_app_replicas_requested', wanted),
                           ('autokom_ha_app_replicas_healthy', count),
                           ('autokom_ha_app_http_ready', http_ready),
                           ('autokom_ha_app_https_ready', int(item.get('status') == 'PUBLIC_HTTPS_ACTIVE'))):
            lines.append(f'{key}{labels} {value}')
    return '\n'.join(lines) + '\n'


def _write_public_metrics(report, destination=PUBLIC_METRICS):
    payload = _metrics_from_report(report)
    fd, temp = tempfile.mkstemp(prefix='.ak-metrics-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(payload)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def reconcile(*, dry_run=False, public_dns=False):
    apps = committed_apps()
    workers = running_workers()
    if not workers and dry_run:
        raise DeliveryError('No healthy MIG workers')
    # In dry runs the same scaling proposal is reported without any mutation.
    previous = _private_json(REPORT, max_bytes=262144) or {}
    autoscaler_min, autoscaler_max = autoscaler_status()
    capacity = desired_floor(apps, previous, autoscaler_min=autoscaler_min,
                             autoscaler_max=autoscaler_max)
    manifests, decisions = assign(apps, workers)
    # A pending workload must remain visible to the client, not become a false
    # service failure while additional workers bootstrap.
    for decision in decisions:
        if decision['status'] == 'INSUFFICIENT_CAPACITY' and capacity['required'] is not None:
            if (capacity['target'] <= autoscaler_min and
                    any(worker.get('generation') == 'legacy' for worker in workers)):
                decision['status'] = 'WAITING_WORKER_UPGRADE'
            else:
                decision['status'] = 'SCALING_UP'
        elif decision['status'] == 'INSUFFICIENT_CAPACITY' and capacity['required'] is None:
            decision['status'] = 'CAPACITY_LIMIT'
    orphaned = deleted_environments(previous, apps)
    report = {'schema': 1, 'updated': int(time.time()), 'workers': len(workers),
              'applications': decisions, 'cloud_https_ready': False,
              'capacity': capacity, 'deleted_environments': orphaned}
    if dry_run:
        return report
    # Do not replace or unroute a currently public worker release before DNS
    # returns to the healthy primary. Retain the old manifests during its TTL.
    by_environment = {decision['environment']: decision for decision in decisions}
    cutovers = {}
    for saved in ha_dns.tracked() if public_dns else ():
        decision = by_environment.get(saved['environment'], {})
        expected = sorted(w['name'] for w in decision.get('workers', ()))
        if (decision.get('ha') is True and decision.get('run_id') == saved['run_id']
                and saved['host'] in decision.get('hosts', ()) and expected == saved['workers']
                and not saved.get('restored_at')):
            continue
        result = ha_dns.transition(saved['host'], saved['environment'], promote=False)
        if result != 'PRIMARY':
            cutovers[saved['host']] = result
    if cutovers:
        # Do not disturb healthy existing workloads while preserving DNS caches.
        report['dns_fallback'] = cutovers
        for decision in decisions:
            if decision['ha']:
                decision['status'] = 'WAITING_PRIMARY_DNS'
        _atomic_report(report)
        _write_public_metrics(report)
        return report
    if not workers:
        _atomic_report(report)
        _write_public_metrics(report)
        raise DeliveryError('No healthy MIG workers')
    if capacity['target'] != autoscaler_min:
        try:
            apply_capacity_floor(capacity, autoscaler_min)
        except (DeliveryError, OSError, ValueError, urllib.error.URLError):
            # Never sacrifice healthy existing allocations on autoscaler errors.
            capacity['status'] = 'CAPACITY_SCALE_ERROR'
            report['capacity_error'] = True
    # GCS desired-state must be committed before creating app-specific health checks.
    for worker in workers:
        desired = {'schema': 1, 'instance': worker['name'], 'zone': worker['zone'],
                   'applications': manifests[worker['name']]}
        gcs_put('desired/' + worker['name'] + '.json', desired)
    # A project removed in AUTOKOM must stop being routed by the cloud LB.
    # Workers remove its containers when the empty manifest is reconciled.
    if orphaned:
        try:
            unroute_deleted(orphaned)
        except (DeliveryError, OSError, ValueError):
            report['cleanup_error'] = True
            report['pending_deletions'] = orphaned
    for app in apps:
        decision = next((d for d in decisions if d['environment'] == app['environment_id']), None)
        if not decision or decision['status'] != 'ASSIGNED':
            continue
        try:
            backend, group = ensure_backend(app, decision['workers'])
            healthy = backend_healthy(app, group, expected=decision['workers'])
            required = 2 if app['ha'] else 1
            decision['healthy_replicas'] = healthy
            if healthy < required:
                decision['status'] = 'WAITING_FOR_HEALTH'
                for host in app['hosts']:
                    if public_dns and ha_dns.marker(host):
                        ha_dns.transition(host, app['environment_id'], promote=False)
                continue
            route_host(app, backend)
            decision['status'] = 'HTTP_LB_READY_NOT_HTTPS'
            if app['ha'] and public_dns:
                names = sorted(w['name'] for w in decision['workers'])
                outcomes = {host: ha_dns.transition(host, app['environment_id'], app['run_id'],
                                                    promote=True, workers=names) for host in app['hosts']}
                decision['https_by_host'] = outcomes
                for host, value in outcomes.items():
                    if value in ('HTTPS_NOT_READY', 'HTTPS_PROBE_OR_DNS_UNAVAILABLE') and ha_dns.marker(host):
                        ha_dns.transition(host, app['environment_id'], promote=False)
                relevant = [value for value in outcomes.values() if value != 'DNS_NOT_ELIGIBLE']
                if relevant and all(value == 'PUBLIC_HTTPS_ACTIVE' for value in relevant):
                    decision['status'] = 'PUBLIC_HTTPS_ACTIVE'
                elif any(value == 'DNS_PROPAGATING' for value in outcomes.values()):
                    decision['status'] = 'PUBLIC_DNS_PROPAGATING'
        except (DeliveryError, OSError, ValueError) as exc:
            decision['status'] = 'RECONCILE_ERROR_' + type(exc).__name__
    report['cloud_https_ready'] = any(d.get('status') == 'PUBLIC_HTTPS_ACTIVE' for d in decisions)
    report['public_routing_verified'] = report['cloud_https_ready']
    _atomic_report(report)
    _write_public_metrics(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='AUTOKOM HA app delivery controller (verified per-app DNS cutover)')
    parser.add_argument('--public-dns', action='store_true', help='Manage verified HA domains on the reserved GCP listener')
    parser.add_argument('--check', action='store_true', help='No state or Compute mutations')
    args = parser.parse_args(argv)
    if os.geteuid() != 0 and not args.check:
        parser.error('Controller requires root')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOCK, 'a+') as file:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            doc = reconcile(dry_run=args.check, public_dns=args.public_dns)
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if not doc.get('capacity_error') and not doc.get('cleanup_error') and all(item['status'] in ('HTTP_LB_READY_NOT_HTTPS', 'PUBLIC_HTTPS_ACTIVE', 'PUBLIC_DNS_PROPAGATING', 'WAITING_PRIMARY_DNS', 'WAITING_FOR_HEALTH', 'ASSIGNED', 'SCALING_UP', 'WAITING_WORKER_UPGRADE', 'INSUFFICIENT_CAPACITY', 'CAPACITY_LIMIT') for item in doc['applications']) else 3
    except BlockingIOError:
        print('AUTOKOM_HA_CONTROLLER_BUSY')
        return 0
    except (DeliveryError, OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        print('AUTOKOM_HA_CONTROLLER_FAILED ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
