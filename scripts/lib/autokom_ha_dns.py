"""Root-only, per-host DNS transition. The delivery timer is its sole writer."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request

DIRECTORY = Path('/var/lib/autokom/ha/dns-cutovers')
LB_IP = '35.186.239.102'  # Reserved Terraform IPv4, never supplied by a customer.
RECORD_ID = re.compile(r'[a-f0-9]{32}')
HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auto\.ikom\.mg')


def _broker():
    import autokom_platform_broker as broker
    return broker


def ownership(host):
    if not isinstance(host, str) or not HOST.fullmatch(host):
        return None
    owner = _broker().STATE / ('domain-owner-' + host + '.txt')
    if owner.is_symlink() or not owner.is_file():
        return None
    st = owner.stat()
    if st.st_uid != 0 or st.st_mode & 0o022 or st.st_size > 128:
        return None
    return _broker().valid_id(owner.read_text(encoding='utf-8').strip())


def marker(host):
    if not isinstance(host, str) or not HOST.fullmatch(host):
        return None
    if DIRECTORY.is_symlink() or (DIRECTORY.exists() and (
            not DIRECTORY.is_dir() or DIRECTORY.stat().st_uid != 0 or DIRECTORY.stat().st_mode & 0o077)):
        raise ValueError('Unsafe HA state directory')
    path = DIRECTORY / (host + '.json')
    if path.is_symlink():
        raise ValueError('Unsafe HA state file')
    if not path.exists():
        return None
    st = path.stat()
    if not path.is_file() or st.st_uid != 0 or st.st_mode & 0o077 or st.st_size > 2048:
        raise ValueError('Unsafe HA state file')
    doc = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(doc, dict) or not RECORD_ID.fullmatch(str(doc.get('id', ''))) or not isinstance(doc.get('origin'), str)
            or doc.get('ip') != LB_IP or doc.get('host') != host):
        raise ValueError('Invalid HA state')
    _broker().valid_id(doc.get('environment'))
    _broker().valid_id(doc.get('run_id'))
    if (not isinstance(doc.get('workers'), list) or len(doc['workers']) != 2
            or not all(isinstance(name, str) and re.fullmatch(r'autokom-ha-web-[a-z0-9]{1,12}', name) for name in doc['workers'])):
        raise ValueError('Invalid HA placement')
    return doc


def tracked():
    if DIRECTORY.is_symlink():
        raise ValueError('Unsafe HA state directory')
    return [marker(p.stem) for p in sorted(DIRECTORY.glob('*.json')) if HOST.fullmatch(p.stem)]


def _write(host, doc):
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    if DIRECTORY.is_symlink() or DIRECTORY.stat().st_uid != 0 or DIRECTORY.stat().st_mode & 0o077:
        raise ValueError('Unsafe HA state directory')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=DIRECTORY, delete=False) as fp:
        temp = Path(fp.name)
        try:
            os.fchmod(fp.fileno(), 0o600)
            json.dump(doc, fp)
            fp.flush()
            os.fsync(fp.fileno())
            os.replace(temp, DIRECTORY / (host + '.json'))
        finally:
            temp.unlink(missing_ok=True)


def forget(host):
    if HOST.fullmatch(host) and not DIRECTORY.is_symlink():
        (DIRECTORY / (host + '.json')).unlink(missing_ok=True)


def accepted_ip(host, origin, environment=None, record_id=None):
    """Broker may accept this alternate only for its own tracked domain."""
    saved = marker(host)
    if (saved and saved['origin'] == origin and ownership(host) == saved['environment']
            and (environment is None or environment == saved['environment'])
            and (record_id is None or record_id == saved['id'])):
        return LB_IP
    return None


def _request(config, path='', query=None, payload=None):
    broker = _broker()
    base = 'https://api.cloudflare.com/client/v4/zones/' + config['zone'] + '/dns_records'
    url = base + path + ('?' + urlencode(query) if query else '')
    req = Request(url, headers={'Authorization': 'Bearer ' + config['token'],
                                'Content-Type': 'application/json'},
                  data=json.dumps(payload).encode() if payload is not None else None,
                  method='PATCH' if payload is not None else 'GET')
    with broker.build_opener(broker.NoRedirect()).open(req, timeout=6) as reply:
        doc = json.loads(reply.read(131073))
    if not isinstance(doc, dict) or doc.get('success') is not True:
        raise ValueError('DNS provider rejected operation')
    return doc.get('result')


def _record(config, host):
    records = _request(config, query={'name': host, 'per_page': 100})
    if not isinstance(records, list) or len(records) >= 100:
        raise ValueError('DNS reply invalid')
    return [r for r in records if isinstance(r, dict) and r.get('type') in ('A', 'AAAA', 'CNAME')
            and str(r.get('name', '')).rstrip('.').lower() == host]


def _dns_at(host, ip):
    # The two independent recursive answers must each be exclusive (not just an
    # intersection). Do not treat timeouts, stale or mixed A answers as verified.
    broker = _broker()
    for resolver in ('1.1.1.1', '8.8.8.8'):
        result = broker.run(['dig', '@' + resolver, '+short', '+time=2', '+tries=1', 'A', host],
                            timeout=4, max_output=3000)
        if {line.strip() for line in result.splitlines() if line.strip()} != {ip}:
            return False
    return True


def _version_is_healthy(host, path):
    broker = _broker()
    context = broker.ssl.create_default_context()
    with broker.socket.create_connection((LB_IP, 443), timeout=6) as raw:
        with context.wrap_socket(raw, server_hostname=host) as conn:
            conn.settimeout(6)
            conn.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            with broker.http.client.HTTPResponse(conn) as reply:
                reply.begin()
                return reply.status == 200 and reply.read(4) == b'ok\n'


def _change(host, env, run_id, promote, workers, retiring=False):
    broker = _broker()
    config = broker._cloudflare_settings()
    origin = config['address']
    if config['suffix'] != 'auto.ikom.mg' or not HOST.fullmatch(host) or host in (
            'pre.auto.ikom.mg', 'backend.auto.ikom.mg', 'www.auto.ikom.mg'):
        return 'DNS_NOT_ELIGIBLE'
    saved = marker(host)
    if ownership(host) != env:
        if saved and not _record(config, host):
            forget(host)
            return 'PRIMARY'
        return 'DNS_OWNERSHIP_MISMATCH'
    if saved and (saved['environment'] != env or saved['origin'] != origin):
        return 'DNS_OWNERSHIP_MISMATCH'
    records = _record(config, host)
    if len(records) != 1:
        return 'DNS_RECORD_CONFLICT'
    record = records[0]
    rid, current = record.get('id'), record.get('content')
    if (record.get('type') != 'A' or record.get('proxied') is not False
            or not isinstance(rid, str) or not RECORD_ID.fullmatch(rid)
            or (saved and saved['id'] != rid)
            or current not in ((origin, LB_IP) if saved else (origin,))
            or record.get('comment') == 'AUTOKOM preprod managed A record'):
        return 'DNS_RECORD_CONFLICT'
    state = broker._load_state(env)
    if not retiring and (not state or (broker.STATE / env / 'previous.json').exists()):
        return 'RELEASE_IN_PROGRESS'
    health = (state or {}).get('healthcheck', '/')
    if not retiring and (not isinstance(health, str) or not re.fullmatch(r'/[a-zA-Z0-9/_~.\-]{0,120}', health) or health.startswith('//')):
        return 'HTTPS_NOT_READY'
    if promote:
        if (state.get('run_id') != run_id or state.get('high_availability_requested') is not True
                or state.get('cloud_delivery_requested') is not True
                or state.get('variables_present') or state.get('stack_project_id')):
            return 'RELEASE_IN_PROGRESS'
        version_path = '/__autokom_health/' + env + '/' + broker.valid_id(run_id)
        if (not _version_is_healthy(host, version_path)
                or not 200 <= broker._customer_https_probe_once(host, health, LB_IP) < 400):
            return 'HTTPS_NOT_READY'
    if not retiring:
        if promote and not broker._usable_customer_certificate(host):
            return 'PRIMARY_NOT_READY'
        if not 200 <= broker._customer_https_probe_once(host, health, broker._customer_https_probe_address()) < 400:
            return 'PRIMARY_NOT_READY'
    target = LB_IP if promote else origin
    ttl = record.get('ttl')
    if type(ttl) is not int or not 1 <= ttl <= 300:
        return 'DNS_TTL_UNSUPPORTED'
    if not promote and saved and (current != target or not saved.get('restored_at')):
        saved = {**saved, 'restored_at': int(time.time()), 'ttl': 300 if ttl == 1 else ttl}
        _write(host, saved)
    if current != target:
        if promote and saved is None:
            _write(host, {'host': host, 'environment': env, 'origin': origin, 'ip': LB_IP, 'id': rid,
                          'run_id': run_id, 'workers': workers})
        result = _request(config, '/' + rid, payload={'content': target})
        if not isinstance(result, dict) or result.get('id') != rid or result.get('content') != target:
            raise ValueError('DNS change not confirmed')
        if not retiring:
            # Wait for a subsequent reconciliation after the write.
            return 'DNS_PROPAGATING' if promote else 'PRIMARY_PROPAGATING'
    if retiring:
        forget(host)
        return 'PRIMARY'
    if not _dns_at(host, target):
        return 'DNS_PROPAGATING' if promote else 'PRIMARY_PROPAGATING'
    if promote:
        return 'PUBLIC_HTTPS_ACTIVE'
    if saved and time.time() < saved['restored_at'] + saved['ttl']:
        return 'PRIMARY_PROPAGATING'
    forget(host)
    return 'PRIMARY'


def transition(host, env, run_id=None, *, promote, workers=None):
    broker = _broker()
    if broker.PREPROD:
        return 'DNS_NOT_ELIGIBLE'
    if promote and (not isinstance(workers, list) or len(workers) != 2 or len(set(workers)) != 2):
        return 'DNS_NOT_ELIGIBLE'
    try:
        return broker._runtime_lock_call(lambda _: _change(host, env, run_id, promote, workers), {})
    except (OSError, ValueError, RuntimeError, broker.BrokerError, broker.http.client.HTTPException):
        # No raw provider or TLS error (potentially credential-bearing) in reports.
        return 'HTTPS_PROBE_OR_DNS_UNAVAILABLE'
