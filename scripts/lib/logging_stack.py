#!/usr/bin/env python3
"""AUTOKOM dual log backend: repeatable, non-destructive Docker deployment.

The OpenSearch service uses official demo TLS certificates as a *bootstrap*
profile. Host ports remain bound to 127.0.0.1; replace demo PKI and create a
least-privilege ingestion identity before any network exposure/production use.
"""
from __future__ import annotations

import argparse
import base64
import ssl
import time
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from cli_colors import pretty_print as print
from hashlib import sha256

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path(os.environ.get('AUTOKOM_LOGS_DIR', '/etc/autokom/logs'))
MANIFEST = '.autokom-managed.json'
PREFERENCE = Path('/etc/autokom/state/logs-backend.json')
# These versions are pinned deliberately. Upgrade only after compatibility testing.
IMAGES = {'loki': 'grafana/loki:3.7.0', 'alloy': 'grafana/alloy:v1.11.0',
          'opensearch': 'opensearchproject/opensearch:3.9.0',
          'dashboards': 'opensearchproject/opensearch-dashboards:3.9.0',
          'prepper': 'opensearchproject/data-prepper:2.13.0'}
BACKENDS = ('loki', 'opensearch', 'both')

class LogError(RuntimeError):
    pass


def run(cmd, *, timeout=90):
    # Existing AUTOKOM spinner; only visible for real TTY, never for --check.
    from tool_banner import Activity
    with Activity('Exécution : '+ ' '.join(str(x) for x in cmd[:2])):
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)


def docker(*args):
    return ['docker', 'compose', '-f', str(STATE_DIR / 'compose.yaml'), '--env-file', str(STATE_DIR / '.env'), *args]


def platform_resources():
    mem = 0
    try:
        match = re.search(r'^MemTotal:\s+(\d+)\s+kB', Path('/proc/meminfo').read_text(), re.M)
        mem = int(match.group(1)) * 1024 if match else 0
    except (OSError, ValueError):
        pass
    target = STATE_DIR if STATE_DIR.exists() else Path('/etc') if Path('/etc').exists() else Path('/')
    free = shutil.disk_usage(target).free
    return mem, free


def resource_warnings(backend):
    mem, disk = platform_resources()
    required = 12 if backend == 'both' else 6 if backend == 'opensearch' else 2
    messages = []
    if mem and mem < required * 1024**3:
        messages.append(f'RAM insuffisante : {mem/1024**3:.1f} GiB ; recommandé >= {required} GiB pour {backend}')
    if disk < 20 * 1024**3:
        messages.append(f'Espace libre : {disk/1024**3:.1f} GiB ; conseillé >= 20 GiB pour logs')
    return messages


def password(existing):
    if existing.exists():
        if existing.is_symlink() or existing.stat().st_mode & 0o077:
            raise LogError('.env doit être un fichier régulier privé (0600)')
        data = existing.read_text()
        match = re.fullmatch(r'OPENSEARCH_INITIAL_ADMIN_PASSWORD=([^\n]+)\n', data)
        if not match:
            raise LogError('.env existant invalide : refus de réinitialiser le mot de passe OpenSearch')
        return match.group(1)
    # Upper/lower/digit/special guaranteed; never display secrets in command output.
    return 'Ak1!' + secrets.token_hex(24) + 'zZ9!'


def yaml_loki():
    return '''auth_enabled: false
server:
  http_listen_port: 3100
  log_level: warn
  # /metrics: Loki self-monitoring via private loopback
common:
  path_prefix: /loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
schema_config:
  configs:
    - from: '2024-01-01'
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  retention_period: 168h
  ingestion_rate_mb: 4
  ingestion_burst_size_mb: 8
compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
analytics:
  reporting_enabled: false
'''


def alloy_config(backend):
    # One collector, two independent outputs. Both are continuously populated
    # in dual mode, while the admin's preference changes the UI/query backend.
    targets = ['loki.write.autokom.receiver'] if backend in ('loki','both') else []
    if backend in ('opensearch','both'):
        targets.append('otelcol.receiver.loki.opensearch.receiver')
    forward = '[' + ', '.join(targets) + ']'
    pieces = ['''logging {
  level = "warn"
}

discovery.docker "autokom" {
  host = "unix:///var/run/docker.sock"
}

discovery.relabel "autokom_docker" {
  targets = discovery.docker.autokom.targets
  rule {
    source_labels = ["__meta_docker_container_label_com_autokom_logs_exclude"]
    regex = "true"
    action = "drop"
  }
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex = "/(.*)"
    target_label = "container"
  }
  rule {
    source_labels = ["__meta_docker_container_label_com_docker_compose_service"]
    target_label = "compose_service"
  }
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex = "/(.+)"
    target_label = "service_name"
  }
  rule {
    source_labels = ["__meta_docker_container_label_com_docker_compose_service"]
    regex = "(.+)"
    target_label = "service_name"
  }
}

loki.relabel "journal" {
  forward_to = []
  rule {
    source_labels = ["__journal__systemd_unit"]
    target_label = "unit"
  }
  rule {
    source_labels = ["__journal__systemd_unit"]
    regex = "(.+)"
    target_label = "service_name"
  }
  rule {
    source_labels = ["__journal_priority_keyword"]
    target_label = "level"
  }
}
''']
    pieces.append('''loki.source.docker "containers" {
  host = "unix:///var/run/docker.sock"
  targets = discovery.relabel.autokom_docker.output
  labels = {platform = "autokom", source = "docker"}
  forward_to = [loki.process.autokom_sanitize.receiver]
}

loki.source.journal "host" {
  max_age = "2h"
  labels = {platform = "autokom", source = "systemd", service_name = "systemd"}
  relabel_rules = loki.relabel.journal.rules
  forward_to = [loki.process.autokom_sanitize.receiver]
}

local.file_match "nginx" {
  path_targets = [{__path__ = "/var/log/nginx/*.log", platform = "autokom", source = "nginx", service_name = "nginx"}]
}

loki.source.file "nginx" {
  targets = local.file_match.nginx.targets
  forward_to = [loki.process.autokom_sanitize.receiver]
}
''')
    pieces.append(r'''// Sanitization is best-effort: applications must still avoid emitting secrets.
loki.process "autokom_sanitize" {
  forward_to = ''' + forward + r'''
  stage.replace {
    expression = "(?i)(?P<secret>authorization:\\s*bearer\\s+[^\\s\\\"]+)"
    replace = "[REDACTED_AUTHORIZATION]"
  }
  stage.replace {
    expression = "(?i)(?P<secret>(password|passwd|api[_-]?key|secret|token)\\s*[:=]\\s*[^\\s,;\\\"]+)"
    replace = "[REDACTED_CREDENTIAL]"
  }
  stage.json {
    expressions = { level = "level", trace_id = "trace_id", request_id = "request_id" }
    drop_malformed = false
  }
  stage.labels {
    values = {level = "level"}
  }
  stage.structured_metadata {
    values = {trace_id = "trace_id", request_id = "request_id"}
  }
}
''')
    if backend in ('loki','both'):
        pieces.append('''loki.write "autokom" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
''')
    if backend in ('opensearch','both'):
        pieces.append('''otelcol.receiver.loki "opensearch" {
  output {
    logs = [otelcol.exporter.otlp.opensearch.input]
  }
}

otelcol.exporter.otlp "opensearch" {
  client {
    endpoint = "data-prepper:21892"
    tls {
      insecure = true
    }
  }
}
''')
    return '\n'.join(pieces)


def data_prepper_pipeline(secret):
    # This is bootstrap-only: admin auth + demo certificate. Restricted file 0600.
    return f'''autokom-logs:
  source:
    otel_logs_source:
      port: 21892
      ssl: false
      output_format: otel
      thread_count: 4
  buffer:
    bounded_blocking:
      buffer_size: 1024
      batch_size: 100
  sink:
    - opensearch:
        hosts: ["https://opensearch:9200"]
        username: "admin"
        password: "{secret}"
        insecure: true
        index_type: custom
        index: "autokom-logs-%{{yyyy.MM.dd}}"
        flush_timeout: -1
  workers: 2
'''


def compose(backend):
    # Profiles allow an independent installation without silently removing the
    # other stack. Never publish APIs to 0.0.0.0 / AWS public interfaces.
    content = f'''# AUTOKOM MANAGED: logging/compose.yaml
name: autokom-logs
services:
  loki:
    profiles: [loki]
    image: {IMAGES['loki']}
    restart: unless-stopped
    command: ["-config.file=/etc/loki/loki.yaml"]
    volumes:
      - ./loki.yaml:/etc/loki/loki.yaml:ro
      - loki-data:/loki
    ports: ["127.0.0.1:3100:3100"]
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging, loopback]
  alloy:
    image: {IMAGES['alloy']}
    restart: unless-stopped
    user: "0:0"
    command: ["run", "--server.http.listen-addr=0.0.0.0:12345", "--storage.path=/var/lib/alloy/data", "/etc/alloy/config.alloy"]
    volumes:
      - ./config.alloy:/etc/alloy/config.alloy:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /var/log/journal:/var/log/journal:ro
      - /run/log/journal:/run/log/journal:ro
      - /var/log/nginx:/var/log/nginx:ro
      - alloy-data:/var/lib/alloy/data
    ports: ["127.0.0.1:12345:12345"]
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging, loopback]
  opensearch:
    profiles: [opensearch]
    image: {IMAGES['opensearch']}
    restart: unless-stopped
    environment:
      - discovery.type=single-node
      - cluster.name=autokom-logs
      - bootstrap.memory_lock=true
      - OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g
      - OPENSEARCH_INITIAL_ADMIN_PASSWORD=${{OPENSEARCH_INITIAL_ADMIN_PASSWORD}}
    ulimits:
      memlock: {{soft: -1, hard: -1}}
      nofile: {{soft: 65536, hard: 65536}}
    volumes: [opensearch-data:/usr/share/opensearch/data]
    ports: ["127.0.0.1:9200:9200"]
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging, loopback]
  opensearch-dashboards:
    profiles: [opensearch]
    image: {IMAGES['dashboards']}
    restart: unless-stopped
    environment:
      - 'OPENSEARCH_HOSTS=["https://opensearch:9200"]'
    ports: ["127.0.0.1:5601:5601"]
    depends_on: [opensearch]
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging, loopback]
  data-prepper:
    profiles: [opensearch]
    image: {IMAGES['prepper']}
    restart: unless-stopped
    user: "0:0"
    environment:
      - JAVA_OPTS=-Xms256m -Xmx512m
    volumes:
      - ./pipelines.yaml:/usr/share/data-prepper/pipelines/pipelines.yaml:ro
    depends_on: [opensearch]
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging]
volumes:
  loki-data:
  alloy-data:
  opensearch-data:
networks:
  logging:
    internal: true
  # Docker Engine 29.x peut ignorer ports: sur un service uniquement
  # connecte a un bridge internal. Ce second reseau assure le NAT host.
  # Les publications restent limitees a 127.0.0.1 (jamais WAN).
  loopback:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.host_binding_ipv4: "127.0.0.1"
'''
    return content



def opensearch_ism_policy():
    """Install retention on new indexes using OpenSearch Index State Management."""
    return {'policy': {
        'description': 'AUTOKOM MANAGED logs retention 14 days',
        'default_state': 'hot',
        'states': [
            {'name':'hot', 'actions':[], 'transitions':[{'state_name':'delete',
                 'conditions':{'min_index_age':'14d'}}]},
            {'name':'delete','actions':[{'delete':{}}],'transitions':[]}],
        'ism_template':[{'index_patterns':['autokom-logs-*'],'priority':100}]
    }}


def provision_retention(secret, *, timeout=90):
    """Provision an isolated-host OpenSearch policy; demo PKI is explicitly untrusted."""
    endpoint = 'https://127.0.0.1:9200/_plugins/_ism/policies/autokom-logs-retention'
    auth = 'Basic '+base64.b64encode(('admin:'+secret).encode()).decode()
    ctx = ssl._create_unverified_context()  # The official demo cert; never public-facing.
    headers = {'Authorization':auth,'Content-Type':'application/json'}
    deadline=time.monotonic()+timeout
    while True:
        request=urllib.request.Request(endpoint,headers=headers,method='GET')
        try:
            with urllib.request.urlopen(request,context=ctx,timeout=5) as response:
                obj=json.load(response)
            desc=obj.get('policy',{}).get('description','')
            if desc != opensearch_ism_policy()['policy']['description']:
                raise LogError('Politique ISM existante non gérée : refus de modification')
            return
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                payload=json.dumps(opensearch_ism_policy()).encode()
                request=urllib.request.Request(endpoint,data=payload,headers=headers,method='PUT')
                try:
                    with urllib.request.urlopen(request,context=ctx,timeout=10) as response:
                        if response.status not in (200,201): raise LogError('ISM OpenSearch HTTP '+str(response.status))
                    return
                except (OSError,urllib.error.URLError) as e:
                    last=str(type(e).__name__)
            elif exc.code in (401,403):
                raise LogError('OpenSearch ISM : authentification admin refusée') from exc
            else:
                last='HTTP '+str(exc.code)
        except (OSError,urllib.error.URLError,ValueError):
            last='API OpenSearch indisponible'
        if time.monotonic()>=deadline:
            raise LogError('OpenSearch lancé mais rétention ISM non confirmée ('+last+'). Refaire install --only logs après contrôle des conteneurs.')
        time.sleep(3)

def grafana_datasource():
    return '''# AUTOKOM MANAGED: loki datasource
apiVersion: 1
datasources:
  - name: AUTOKOM Loki
    uid: autokom-loki
    type: loki
    access: proxy
    url: http://127.0.0.1:3100
    isDefault: false
    editable: false
    jsonData:
      maxLines: 1000
'''


def grafana_dashboard():
    panel = {'id': 1, 'title': 'Journaux des services', 'type': 'logs',
             'datasource': {'type':'loki','uid':'autokom-loki'},
             'gridPos': {'x':0,'y':0,'w':24,'h':14},
             'targets':[{'refId':'A','expr':'{platform="autokom"} |~ "$search"', 'queryType':'range'}]}
    dashboard = {'uid':'autokom-logs','title':'AUTOKOM · Journaux',
                 'tags':['autokom-managed','autokom','logs'],'schemaVersion':39,
                 'refresh':'30s','editable':False,'time':{'from':'now-1h','to':'now'},
                 'templating':{'list':[{'name':'search','type':'textbox','label':'Recherche (regex)',
                                          'query':'','current':{'text':'','value':''}}]},
                 'panels':[panel]}
    return json.dumps(dashboard, indent=2, ensure_ascii=False) + '\n'


def files_for(backend, secret):
    return {
        'compose.yaml': (compose(backend), 0o644),
        'loki.yaml': (yaml_loki(),0o644),
        'config.alloy': (alloy_config(backend),0o644),
        'pipelines.yaml': (data_prepper_pipeline(secret),0o600),
    }


def sha(data):
    return sha256(data.encode()).hexdigest()


def read_manifest():
    path = STATE_DIR / MANIFEST
    if not path.exists():
        return {}
    if path.is_symlink():
        raise LogError('Manifest symbolique refusé')
    try:
        obj = json.loads(path.read_text())
        if obj['schema'] != 1 or not isinstance(obj['files'],dict):
            raise ValueError()
        return obj['files']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LogError('Manifest AUTOKOM inconnu ou endommagé') from exc


def validate_managed(expected):
    manifest = read_manifest()
    for name in expected:
        dest=STATE_DIR/name
        if dest.is_symlink():
            raise LogError('Refus de lien symbolique : '+str(dest))
        if dest.exists():
            if name not in manifest or sha(dest.read_text()) != manifest[name]:
                raise LogError('Fichier modifié/non géré : '+str(dest)+' ; aucune réécriture')
    return manifest


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.autokom-', dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            out.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def install_files(backend):
    if STATE_DIR.is_symlink():
        raise LogError('Répertoire de configuration symbolique refusé')
    STATE_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(STATE_DIR,0o700)
    env=STATE_DIR/'.env'
    secret=password(env)
    expected=files_for(backend,secret)
    previous=validate_managed(expected)
    for name,(content,mode) in expected.items():
        if not (STATE_DIR/name).exists() or (STATE_DIR/name).read_text()!=content:
            atomic_write(STATE_DIR/name,content,mode)
    if not env.exists():
        atomic_write(env, 'OPENSEARCH_INITIAL_ADMIN_PASSWORD='+secret+'\n',0o600)
    elif env.stat().st_mode & 0o777 != 0o600:
        raise LogError('Permissions .env invalides')
    manifest = {name:sha(content) for name,(content,_) in expected.items()}
    if manifest != previous:
        atomic_write(STATE_DIR/MANIFEST,json.dumps({'schema':1,'files':manifest},indent=2)+'\n',0o600)
    return secret


def preflight(backend):
    if not shutil.which('docker'):
        raise LogError('Docker absent ; installer Foundation avant les logs')
    p=run(['docker','compose','version'])
    if p.returncode: raise LogError('Docker Compose v2 requis')
    p=run(['docker','info','--format','{{.ServerVersion}}'])
    if p.returncode: raise LogError('Daemon Docker non joignable ; root/docker requis')
    if backend in ('opensearch','both'):
        try: limit=int(Path('/proc/sys/vm/max_map_count').read_text().strip())
        except (OSError,ValueError): limit=0
        if limit < 262144:
            raise LogError('OpenSearch exige vm.max_map_count >= 262144. Configurer sysctl explicitement avant installation')
    for msg in resource_warnings(backend):
        print('  ! '+msg)
    ram, _ = platform_resources()
    min_ram = 8 if backend == 'both' else 5 if backend == 'opensearch' else 1
    if ram and ram < min_ram * 1024**3:
        raise LogError('Ressources insuffisantes : installation annulée pour protéger Jenkins et Grafana')


def validate_alloy(backend):
    """Validate generated Alloy components before replacing live configuration.

    Uses the exact pinned image, without a network or running collector.
    This checks River syntax and components, not actual Docker/journal access.
    """
    with tempfile.TemporaryDirectory(prefix='autokom-alloy-') as tmp:
        filename=Path(tmp)/'config.alloy'
        filename.write_text(alloy_config(backend))
        os.chmod(tmp,0o755)
        os.chmod(filename,0o644)
        p=run(['docker','run','--rm','--network','none','--read-only',
               '-v',str(filename)+':/etc/alloy/config.alloy:ro',
               IMAGES['alloy'],'validate','/etc/alloy/config.alloy'],timeout=180)
        if p.returncode:
            # Validation output may reflect snippets of configuration. Never
            # include it in CLI logs because future pipelines may contain secrets.
            raise LogError('Validation Alloy échouée avant déploiement ; aucun fichier live remplacé. Vérifier la compatibilité du générateur avec l’image épinglée.')


def profile_args(backend):
    result=[]
    if backend in ('loki','both'): result += ['--profile','loki']
    if backend in ('opensearch','both'): result += ['--profile','opensearch']
    return result


def desired_services(backend):
    return ['alloy'] + (['loki'] if backend in ('loki','both') else []) + (
        ['opensearch','opensearch-dashboards','data-prepper'] if backend in ('opensearch','both') else [])


def ready(backend):
    # Runtime check is not proof of ingestion. See test e2e in docs.
    if not shutil.which('docker') or not (STATE_DIR/'compose.yaml').exists(): return False
    try:
        p=run(docker(*profile_args(backend),'ps','--status','running','--services'),timeout=15)
    except (OSError,subprocess.TimeoutExpired):return False
    if p.returncode or not set(desired_services(backend))<=set(p.stdout.splitlines()):
        return False
    # Docker peut déclarer `running` sans publier ses ports (bridge internal).
    # Les commandes install/status ne doivent donc jamais dire conforme sans HTTP.
    from logging_health import http_status
    if backend in ('loki','both'):
        return http_status('loki')['ok'] and http_status('alloy')['ok']
    return http_status('alloy')['ok']


def installed_backend():
    """Determine installed outputs from the current managed Alloy config."""
    from logging_runtime import detected, NATIVE
    path = (NATIVE / 'config.alloy') if detected()=='systemd' else (STATE_DIR / 'config.alloy')
    if not path.is_file() or path.is_symlink():
        raise LogError('Installation logs absente ; exécuter sudo autokom install logs')
    data=path.read_text()
    loki='loki.write "autokom"' in data
    search='otelcol.receiver.loki "opensearch"' in data
    if loki and search:return 'both'
    if loki:return 'loki'
    if search:return 'opensearch'
    raise LogError('Configuration Alloy non reconnue : réconciliation refusée')

def backend_state():
    try:
        v=json.loads(PREFERENCE.read_text())['backend']
        return v if v in ('loki','opensearch') else 'loki'
    except (FileNotFoundError,ValueError,KeyError,OSError,TypeError):
        return 'loki'


def use_backend(name):
    if name not in ('loki','opensearch'): raise LogError('Backend inconnu : '+name)
    from logging_runtime import detected, runtime_ready
    mode = detected()
    if not runtime_ready(name, mode): raise LogError('Backend non démarré ; vérifier sudo autokom logs doctor')
    PREFERENCE.parent.mkdir(parents=True,exist_ok=True)
    if PREFERENCE.is_symlink(): raise LogError('État symbolique non autorisé')
    atomic_write(PREFERENCE,json.dumps({'schema':1,'backend':name},indent=2)+'\n',0o644)
    print('  ✓ Moteur de consultation préféré : '+name)
    print('  › Les deux stockages continuent de collecter si installés ; les anciens logs ne sont pas migrés.')



GRAFANA_PROVIDER = Path('/etc/grafana/provisioning/dashboards/autokom.yaml')
GRAFANA_DASH_DIRECTORY = Path('/var/lib/grafana/dashboards/autokom')


def provider_content():
    # Same provider / folder / path as the observability module.  Do not
    # register a second provider for the same dashboards, or duplicate UIDs.
    return ("# AUTOKOM MANAGED dashboards\n"
            "apiVersion: 1\nproviders:\n"
            "  - name: autokom\n    orgId: 1\n    folder: AUTOKOM\n"
            "    type: file\n    disableDeletion: true\n"
            "    allowUiUpdates: false\n    updateIntervalSeconds: 30\n"
            "    options:\n      path: /var/lib/grafana/dashboards/autokom\n")


def verify_provider(path=None):
    """Read-only check: existing providers belong to observability or AUTOKOM.

    Never overwrite a provider owned by another module/operator.
    """
    path = GRAFANA_PROVIDER if path is None else path
    if path.is_symlink():raise LogError('Lien symbolique provider Grafana refusé')
    if not path.is_file():return False
    try:
        content=path.read_text()
        import yaml
        try:doc=yaml.safe_load(content)
        except yaml.YAMLError as exc:raise LogError('Provider Grafana YAML invalide') from exc
        entries=doc.get('providers',[]) if isinstance(doc,dict) else []
        matches=[x for x in entries if isinstance(x,dict) and x.get('name')=='autokom']
        if len(matches)!=1 or matches[0].get('type')!='file' or matches[0].get('options',{}).get('path')!=str(GRAFANA_DASH_DIRECTORY):
            raise LogError('Fournisseur Grafana autokom existant incohérent : intervention manuelle requise')
    except ImportError:
        # No PyYAML required by the CLI itself.  Conservative validation.
        if ('name: autokom' not in content or str(GRAFANA_DASH_DIRECTORY) not in content):
            raise LogError('Fournisseur Grafana existant non vérifiable sans PyYAML')
    except (OSError,ValueError,TypeError,AttributeError) as exc:
        raise LogError('Fournisseur Grafana invalide') from exc
    return True


def ensure_provider():
    """Create the provider only when absent; preserve observability-owned file."""
    if verify_provider():return False
    atomic_write(GRAFANA_PROVIDER,provider_content(),0o644)
    return True


def provision_grafana():
    """Write only AUTOKOM-owned Grafana objects; protect operator changes.

    First upgrade accepts the exact v20 dashboard. Later upgrades compare
    checksums recorded at last AUTOKOM write, never just a cosmetic tag.
    """
    if not Path('/etc/grafana/provisioning').exists():
        print('  ! Grafana absent : provisioning en attente (ne bloque pas Loki).')
        return
    from logging_dashboards import serialized
    ds=Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml')
    old_dash=Path('/var/lib/grafana/dashboards/autokom/autokom-logs.json')
    mapping = {ds:grafana_datasource()}
    for filename, content in serialized().items():
        mapping[old_dash.parent/filename]=content
    state=STATE_DIR/'.grafana-managed.json'
    if state.is_symlink():raise LogError('Manifest Grafana symbolique non autorisé')
    try:
        manifest=json.loads(state.read_text())['files'] if state.exists() else {}
        if not isinstance(manifest,dict):raise ValueError()
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise LogError('Manifest Grafana invalide : intervention requise') from exc
    # Validate the shared provider *before* any write. The original provider
    # from `install observability` is left byte-for-byte unchanged.
    verify_provider()
    # Read-only preflight of every resource before any file is changed.
    for path,content in mapping.items():
        if path.is_symlink():raise LogError('Symlink Grafana refusé : '+str(path))
        if not path.exists():continue
        existing=path.read_text()
        prior=manifest.get(str(path))
        if prior:
            if prior!=sha(existing):
                raise LogError('Dashboard/datasource modifié manuellement : '+str(path))
        elif existing!=content and not (path==old_dash and existing==grafana_dashboard()):
            raise LogError('Grafana existant non reconnu : '+str(path)+' ; refus d’écrasement')
    # Record a byte-for-byte backup of the canonical v20 dashboard before migration.
    if old_dash.is_file() and old_dash.read_text()==grafana_dashboard():
        backup=old_dash.with_name('autokom-logs.v20-backup')
        if not backup.exists():atomic_write(backup,old_dash.read_text(),0o600)
    provider_created=ensure_provider()
    changed=provider_created
    for path,content in mapping.items():
        if not path.exists() or path.read_text()!=content:
            atomic_write(path,content,0o644)
            changed=True
    atomic_write(state,json.dumps({'schema':1,'files':{str(path):sha(content)
                                            for path,content in mapping.items()}},indent=2)+'\n',0o600)
    print('  ✓ Grafana : datasource Loki et 4 dashboards provisionnés (fichiers).')
    print('  › Provider Grafana vérifié : /etc/grafana/provisioning/dashboards/autokom.yaml')
    return changed




def install_complete(backend, mode):
    """Read-only installation completeness; includes Grafana, not just processes."""
    from logging_runtime import runtime_ready
    if not runtime_ready(backend, mode):return False
    if backend not in ('loki','both'):return True
    if not Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml').is_file():
        return False
    try:
        if not verify_provider():return False
    except LogError:
        return False
    from logging_dashboards import serialized
    if not all((GRAFANA_DASH_DIRECTORY/name).is_file() for name in serialized()):return False
    # Compare AUTOKOM's last managed hashes; a manual edit means the phase is
    # no longer conformant, but remediation still refuses to overwrite it.
    manifest_file=STATE_DIR/'.grafana-managed.json'
    if not manifest_file.is_file() or manifest_file.is_symlink():return False
    try:
        managed=json.loads(manifest_file.read_text())['files']
        if not isinstance(managed,dict):return False
        names=list(serialized())+['autokom-loki.yaml']
        paths=[GRAFANA_DASH_DIRECTORY/name for name in serialized()]
        paths.append(Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml'))
        if any(not file.is_file() or sha(file.read_text())!=managed.get(str(file))
               for file in paths):return False
    except (OSError,TypeError,KeyError,ValueError):return False
    from grafana_bridge import datasource_loaded
    # When SQLite is not the effective Grafana store, do not infer missing.
    return datasource_loaded() is not False


def sync_grafana_after_install(changed=False):
    """Installation/reconciliation includes provisioning, not just file writes.

    A restart is scoped to Grafana; if checks cannot prove healthy API afterwards
    the installation fails with a useful error and never claims full success.
    """
    from grafana_bridge import finalize_install
    return finalize_install(changed=changed)


def apply_docker(backend):
    preflight(backend)
    validate_alloy(backend)
    print('  ✓ Configuration Alloy validée avec l’image conteneur épinglée')
    secret=install_files(backend)
    # Never print secret / JSON with credentials.
    p=run(docker(*profile_args(backend),'config','-q'))
    if p.returncode: raise LogError('Compose invalide (configuration sauvegardée, aucun conteneur démarré). Exécuter docker compose config -q pour le diagnostic local.')
    p=run(docker(*profile_args(backend),'up','-d','--no-build'),timeout=600)
    if p.returncode: raise LogError('Compose a échoué (code '+str(p.returncode)+'). Examiner docker compose logs localement sans publier de secrets.')
    if backend in ('opensearch','both'):
        provision_retention(secret)
        print('  ✓ OpenSearch : politique ISM de rétention 14 jours confirmée')
    if backend in ('loki','both'):
        grafana_changed=provision_grafana()
        from logging_monitoring import apply as monitoring_apply
        monitoring_apply()
    else:
        print('  › OpenSearch seul : aucun dashboard Grafana/Loki inutilisable n’est créé.')
    # Ne jamais annoncer succes sans API joignable sur l'hote.
    from logging_health import http_status
    expected = ('loki','alloy') if backend in ('loki','both') else ('alloy',)
    deadline = time.monotonic() + 45
    broken = list(expected)
    from tool_banner import Activity
    with Activity('Attente des endpoints privés Loki / Alloy'):
        while time.monotonic() < deadline:
            broken = [name for name in expected if not http_status(name)['ok']]
            if not broken:
                break
            time.sleep(3)
    if broken:
        raise LogError('Conteneurs lancés mais endpoints locaux non joignables : '+', '.join(broken)+
                       '. Vérifier autokom logs status et autokom tools show loki/alloy ; '
                       'aucun service tiers n’a été redémarré.')
    if backend in ('loki','both'):
        sync_grafana_after_install(changed=grafana_changed)
    print('  ✓ Services démarrés et API locales vérifiées : '+', '.join(expected))
    print('  › Vérifier ingestion réelle dans Grafana et OpenSearch Dashboards avant déclaration de conformité.')
    return 0



def main(argv=None):
    parser=argparse.ArgumentParser(prog='autokom logging',description='Loki + Alloy et OpenSearch + Data Prepper + Dashboards')
    parser.add_argument('action',nargs='?',choices=('status','use','reconcile','verify','doctor','runtime','audit','grafana'),default='status')
    parser.add_argument('choice',nargs='?',choices=('loki','opensearch','docker','systemd'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--backend',choices=BACKENDS,default='both',help='Composants à installer ; both par défaut')
    parser.add_argument('--runtime',choices=('docker','systemd'),help='Exécution de Loki/Alloy : Docker Compose ou systemd')
    a=parser.parse_args(argv)
    from logging_runtime import detected, runtime_ready, plan as runtime_plan, apply as apply_runtime, report as runtime_report, RuntimeErrorSafe
    if a.action=='grafana':
        from grafana_bridge import reconcile
        return reconcile(apply=a.apply)
    if a.action == 'audit':
        from logging_audit import run_audit
        return run_audit()
    if a.action == 'runtime':
        if not a.choice:
            runtime_report()
            if a.apply:raise LogError('Mode requis : autokom logs runtime docker|systemd --apply')
            return 0
        if a.choice not in ('docker','systemd'):raise LogError('Choisir docker ou systemd')
        runtime_plan(a.choice)
        if a.apply:
            try:apply_runtime(a.choice,'loki')
            except RuntimeErrorSafe as exc:raise LogError(str(exc)) from exc
        return 0
    if a.action=='verify':
        if os.geteuid()!=0:raise LogError('sudo requis pour émettre la sonde et écrire son état')
        if not runtime_ready('loki', detected()):raise LogError('Loki non actif : ne pas lancer une sonde en vain')
        from logs_canary import probe
        return probe()
    if a.action=='reconcile':
        if os.geteuid()!=0:raise LogError('sudo requis')
        current = installed_backend()
        if not a.apply:
            print('  AUTOKOM LOGS / RECONCILIATION — lecture seule : '+current)
            from logging_health import detail_table
            detail_table(current)
            print('  › Pour appliquer sans supprimer de services : sudo autokom logs reconcile --apply')
            return 0
        a.backend=current
        a.runtime=detected() if detected() in ('docker','systemd') else 'docker'
    if a.action=='use':
        if not a.choice: parser.error('use exige loki ou opensearch')
        if os.geteuid()!=0: raise LogError('sudo requis')
        use_backend(a.choice)
        return 0
    if a.apply:
        if os.geteuid()!=0:raise LogError('sudo requis')
        mode = a.runtime or detected()
        if mode in ('none','mixed'): mode = a.runtime or 'docker'
        if mode=='systemd' or (mode=='docker' and detected()=='systemd'):
            try:apply_runtime(mode,a.backend)
            except RuntimeErrorSafe as exc:raise LogError(str(exc)) from exc
            return 0
        if detected() == 'mixed':raise LogError('Modes Docker et systemd en conflit : résolution manuelle nécessaire')
        return apply_docker(a.backend)
    if a.action in ('status','doctor') and (detected()=='systemd' or (STATE_DIR/'config.alloy').is_file()):
        a.backend=installed_backend()
    runtime_report()
    print('  AUTOKOM logs | installation cible : '+a.backend+' | préférence : '+backend_state())
    mode=detected()
    print('  Services actifs : '+('oui' if runtime_ready(a.backend,mode) else 'non / non confirmé'))
    for warning in resource_warnings(a.backend): print('  ! '+warning)
    if a.action in ('status','doctor'):
        from logging_health import detail_table
        detail_table(a.backend)
        if (STATE_DIR/'config.alloy').is_file():
            from logging_monitoring import report
            report()
    print('  Endpoints prévus : Loki http://127.0.0.1:3100 ; OpenSearch https://127.0.0.1:9200 ; Dashboards http://127.0.0.1:5601')
    print('  › Changer le mode : autokom logs runtime ; sudo autokom logs runtime systemd --apply')
    print('  État en lecture seule ; en cas de panne : sudo autokom logs reconcile --check')
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (LogError,OSError,subprocess.TimeoutExpired) as e:
        print('  ✗ '+str(e),file=sys.stderr)
        raise SystemExit(1)
