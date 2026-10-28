#!/usr/bin/env python3
"""Read-only runtime endpoints and Docker port publications for AUTOKOM logs.

Never prints container environment, mounts with secrets, credentials, or tokens.
Containers being `running` is not proof of a host-port binding or readiness.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from terminal_table import Cell, table

# Public metadata; bind addresses always loopback and not AWS public IPs.
SERVICES = {
    'loki': {'port': 3100, 'url': 'http://127.0.0.1:3100',
             'ready': '/ready', 'metrics': '/metrics', 'purpose': 'LogQL, ingestion, stockage'},
    'alloy': {'port': 12345, 'url': 'http://127.0.0.1:12345',
              'ready': '/-/ready', 'metrics': '/metrics', 'purpose': 'Collecte, forwarding, UI interne'},
    'opensearch': {'port': 9200, 'url': 'https://127.0.0.1:9200',
                   'ready': '/', 'metrics': None, 'purpose': 'Recherche ; TLS et authentification'},
    'opensearch-dashboards': {'port': 5601, 'url': 'http://127.0.0.1:5601',
                              'ready': '/', 'metrics': None, 'purpose': 'Exploration OpenSearch'},
    'data-prepper': {'port': None, 'url': 'http://data-prepper:4900',
                      'ready': '/list', 'metrics': '/metrics/sys',
                      'purpose': 'Ingestion OTLP : data-prepper:21892 ; API interne : 4900'},
}


def safe_process(arguments, timeout=5):
    try:
        return subprocess.run(arguments, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def published_ports(service):
    """Return (status, publications). `no mapping` is distinct from `running`."""
    if service not in SERVICES or not shutil.which('docker'):
        return 'indisponible', ()
    p = safe_process(['docker', 'ps', '-a',
                      '--filter', 'label=com.docker.compose.project=autokom-logs',
                      '--filter', f'label=com.docker.compose.service={service}',
                      '--format', '{{.ID}}'])
    if p is None or p.returncode:
        return 'indisponible', ()
    ids = p.stdout.splitlines()
    if not ids:
        return 'absent', ()
    if len(ids) != 1:
        return 'ambigü', ()
    q = safe_process(['docker', 'inspect', '--format',
                      '{{json .NetworkSettings.Ports}}', ids[0]])
    if q is None or q.returncode:
        return 'indisponible', ()
    try:
        data = json.loads(q.stdout)
        if data is None:
            return 'aucune publication', ()
        publications = []
        if not isinstance(data, dict):
            return 'invalide', ()
        for port, mappings in data.items():
            for m in (mappings or []):
                if not isinstance(m, dict):
                    continue
                ip = m.get('HostIp', '')
                hostport = m.get('HostPort', '')
                if hostport and hostport.isdigit() and ip in ('127.0.0.1', '0.0.0.0', '::', '::1'):
                    publications.append(f'{ip}:{hostport} → {port}')
        return ('publié' if publications else 'aucune publication'), tuple(publications)
    except (ValueError, TypeError, AttributeError):
        return 'invalide', ()


def http_status(name, *, timeout=2):
    """No secrets. OpenSearch TLS with demo PKI isn't accepted as verified."""
    details = SERVICES[name]
    if name == 'data-prepper':
        return {'ok': False, 'detail': 'API interne seulement : Docker DNS ; pas de publication hôte'}
    url = details['url'] + details['ready']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as result:
            body = result.read(1500)
            ok = result.status == 200
        if name == 'loki':
            ok = ok and b'ready' in body.lower()
        return {'ok': bool(ok), 'detail': ('HTTP 200 — prêt' if ok else 'réponse HTTP inattendue')}
    except urllib.error.HTTPError as e:
        if name in ('opensearch','opensearch-dashboards') and e.code in (401,403):
            return {'ok': True, 'detail': f'HTTP {e.code} — authentification exigée'}
        return {'ok': False, 'detail': f'HTTP {e.code}'}
    except urllib.error.URLError as e:
        reason = e.reason
        return {'ok': False, 'detail': 'connexion refusée/indisponible' if isinstance(reason, ConnectionRefusedError) else type(reason).__name__}
    except (OSError, ValueError) as e:
        return {'ok': False, 'detail': type(e).__name__}


def runtime_rows(name):
    info = SERVICES[name]
    from logging_runtime import detected
    runtime = detected() if name in ('loki','alloy') else 'docker'
    if runtime=='none':runtime='absent'
    ip_port = f'127.0.0.1:{info["port"]}' if info['port'] else 'non publié sur hôte'
    items = [('Gestionnaire actif', 'Docker Compose' if runtime=='docker' else ('systemd' if runtime=='systemd' else 'aucun')),
             ('Service', name),
             ('Port prévu (hôte)', ip_port),
             ('Endpoint / URL', info['url']),
             ('API de disponibilité', info['ready'] if info['port'] else 'interne /list'),
             ('Endpoint métriques', (info['url'] + info['metrics']) if info['metrics'] else 'Pas de /metrics Prometheus natif'),
             ('Utilisation', info['purpose'])]
    items.insert(0,('Exécution effective',runtime))
    docker_state, mappings = published_ports(name)
    if runtime=='docker':items.append(('Publication Docker effective', ', '.join(mappings) if mappings else docker_state))
    elif runtime=='systemd':items.append(('Unité systemd',name+'.service'))
    if info['port']:
        status = http_status(name)
        items.append(('Disponibilité vérifiée', ('✓ ' if status['ok'] else '! ') + status['detail']))
    else:
        items.append(('Disponibilité vérifiée', 'API interne — non sondée depuis l’hôte'))
    # Distinguish an exposed listener on 0.0.0.0 from host-only exposure.
    if any(not line.startswith(('127.0.0.1:', '::1:')) for line in mappings):
        items.append(('ALERTE RÉSEAU', 'Port publié hors loopback : corriger immédiatement'))
    if runtime=='docker' and info['port'] and docker_state == 'aucune publication':
        items.append(('Diagnostic', 'Conteneur actif sans port publié : vérifier les réseaux Docker internal'))
    return items


def detail_table(backend):
    names = ('alloy', 'loki') if backend == 'loki' else (
        ('alloy', 'opensearch', 'data-prepper', 'opensearch-dashboards') if backend == 'opensearch'
        else ('alloy', 'loki', 'opensearch', 'data-prepper', 'opensearch-dashboards'))
    print('  ◈ AUTOKOM / JOURNALISATION · RÉSEAU & SANTÉ RÉELLE')
    for name in names:
        info=SERVICES[name]
        from logging_runtime import detected
        runtime=detected() if name in ('loki','alloy') else 'docker'
        if runtime=='none':runtime='docker'
        publication, mappings=published_ports(name)
        checked=http_status(name) if info['port'] else {'ok':False,'detail':'réseau Docker privé uniquement'}
        is_private=all(line.startswith(('127.0.0.1:', '::1:')) for line in mappings)
        print(f'\n  › {name}')
        status = ('✓ répond' if checked['ok'] else '! '+checked['detail'])
        rows=[('Mode actif',runtime),('URL / santé', info['url']+info['ready'] if info['port'] else 'interne Docker uniquement'),
              ('Métriques', info['url']+info['metrics'] if info['metrics'] else 'non exposées'),
              ('Port hôte attendu', f'127.0.0.1:{info["port"]}' if info['port'] else 'aucun'),
              ('Publication effective', ', '.join(mappings) if mappings else publication),
              ('API', status)]
        if not is_private:
            rows.append(('SÉCURITÉ', 'ALERTE : publication non limitée à 127.0.0.1'))
        if runtime=='docker' and info['port'] and not mappings and publication == 'aucune publication':
            rows.append(('Action', 'Réseau Docker internal seul : appliquer le correctif v22'))
        table(['Contrôle', 'État observé'], rows)
