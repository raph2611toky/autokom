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
}

loki.relabel "journal" {
  forward_to = []
  rule {
    source_labels = ["__journal__systemd_unit"]
    target_label = "unit"
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
  forward_to = ''' + forward + '''
}

loki.source.journal "host" {
  max_age = "2h"
  labels = {platform = "autokom", source = "systemd"}
  relabel_rules = loki.relabel.journal.rules
  forward_to = ''' + forward + '''
}

local.file_match "nginx" {
  path_targets = [{__path__ = "/var/log/nginx/*.log", platform = "autokom", source = "nginx"}]
}

loki.source.file "nginx" {
  targets = local.file_match.nginx.targets
  forward_to = ''' + forward + '''
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
    networks: [logging]
  alloy:
    image: {IMAGES['alloy']}
    restart: unless-stopped
    user: "0:0"
    command: ["run", "--storage.path=/var/lib/alloy/data", "/etc/alloy/config.alloy"]
    volumes:
      - ./config.alloy:/etc/alloy/config.alloy:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /var/log/journal:/var/log/journal:ro
      - /run/log/journal:/run/log/journal:ro
      - /var/log/nginx:/var/log/nginx:ro
      - alloy-data:/var/lib/alloy/data
    labels: {{com.autokom.logs.exclude: "true"}}
    security_opt: [no-new-privileges:true]
    networks: [logging]
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
    networks: [logging]
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
    networks: [logging]
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
    return p.returncode==0 and set(desired_services(backend))<=set(p.stdout.splitlines())


def backend_state():
    try:
        v=json.loads(PREFERENCE.read_text())['backend']
        return v if v in ('loki','opensearch') else 'loki'
    except (FileNotFoundError,ValueError,KeyError,OSError,TypeError):
        return 'loki'


def use_backend(name):
    if name not in ('loki','opensearch'): raise LogError('Backend inconnu : '+name)
    if not ready(name): raise LogError('Backend non démarré ; lancer sudo autokom install --only logs --backend both')
    PREFERENCE.parent.mkdir(parents=True,exist_ok=True)
    if PREFERENCE.is_symlink(): raise LogError('État symbolique non autorisé')
    atomic_write(PREFERENCE,json.dumps({'schema':1,'backend':name},indent=2)+'\n',0o644)
    print('  ✓ Moteur de consultation préféré : '+name)
    print('  › Les deux stockages continuent de collecter si installés ; les anciens logs ne sont pas migrés.')


def provision_grafana():
    # Only add our own files; no modification of Grafana/Prometheus credentials.
    if not Path('/etc/grafana/provisioning').exists(): return
    mapping = {
        Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml'): (grafana_datasource(), '# AUTOKOM MANAGED'),
        Path('/var/lib/grafana/dashboards/autokom/autokom-logs.json'): (grafana_dashboard(), 'autokom-managed')}
    for path,(content,marker) in mapping.items():
        if path.is_symlink() or path.exists() and marker not in path.read_text():
            raise LogError('Refus de modifier le fichier Grafana tiers : '+str(path))
    for path,(content,_) in mapping.items():
        if not path.exists() or path.read_text()!=content: atomic_write(path,content,0o644)
    print('  › Grafana : datasource Loki et dashboard créés. Rechargement/restart Grafana à valider séparément.')


def main(argv=None):
    parser=argparse.ArgumentParser(prog='autokom logging',description='Loki + Alloy et OpenSearch + Data Prepper + Dashboards')
    parser.add_argument('action',nargs='?',choices=('status','use'),default='status')
    parser.add_argument('choice',nargs='?',choices=('loki','opensearch'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--backend',choices=BACKENDS,default='both',help='Composants à installer ; both par défaut')
    a=parser.parse_args(argv)
    if a.action=='use':
        if not a.choice: parser.error('use exige loki ou opensearch')
        if os.geteuid()!=0: raise LogError('sudo requis')
        use_backend(a.choice)
        return 0
    if a.apply:
        if os.geteuid()!=0: raise LogError('sudo requis')
        preflight(a.backend)
        secret=install_files(a.backend)
        # Never print secret / JSON with credentials.
        p=run(docker(*profile_args(a.backend),'config','-q'))
        if p.returncode: raise LogError('Compose invalide (configuration sauvegardée, aucun conteneur démarré). Exécuter docker compose config -q pour le diagnostic local.')
        p=run(docker(*profile_args(a.backend),'up','-d','--no-build'),timeout=600)
        if p.returncode: raise LogError('Compose a échoué (code '+str(p.returncode)+'). Examiner docker compose logs localement sans publier de secrets.')
        if a.backend in ('opensearch','both'):
            provision_retention(secret)
            print('  ✓ OpenSearch : politique ISM de rétention 14 jours confirmée')
        if a.backend in ('loki','both'):
            provision_grafana()
        else:
            print('  › OpenSearch seul : aucun dashboard Grafana/Loki inutilisable n’est créé.')
        print('  ✓ Services lancés : '+', '.join(desired_services(a.backend)))
        print('  › Vérifier ingestion réelle dans Grafana et OpenSearch Dashboards avant déclaration de conformité.')
        return 0
    print('  AUTOKOM logs | installation cible : '+a.backend+' | préférence : '+backend_state())
    print('  Services actifs : '+('oui' if ready(a.backend) else 'non / non confirmé'))
    for warning in resource_warnings(a.backend): print('  ! '+warning)
    print('  Endpoints prévus : Loki http://127.0.0.1:3100 ; OpenSearch https://127.0.0.1:9200 ; Dashboards http://127.0.0.1:5601')
    print('  Simulation en lecture seule ; pour installer : sudo autokom install --only logs --backend both')
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (LogError,OSError,subprocess.TimeoutExpired) as e:
        print('  ✗ '+str(e),file=sys.stderr)
        raise SystemExit(1)
