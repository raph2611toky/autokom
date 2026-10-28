#!/usr/bin/env python3
"""Catalogue AUTOKOM des outils, chemins et opérations explicitement autorisées.

Ce module n'exécute pas de shell et n'inclut jamais la valeur des secrets dans
les sorties d'inventaire. Les services non gérés restent inspectables.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess
import sys
from configparser import ConfigParser

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from observability_config import load as load_observability, ConfigError, ALLOWED
from tool_banner import colored_marker, section as ui_section
from terminal_table import Cell, table


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    group: str
    info: str
    binaries: tuple[str, ...] = ()
    unit: str = ''
    files: tuple[tuple[str, str], ...] = ()
    docs: str = ''
    critical: bool = False


SPECS = (
    Tool('git','00 · Fondations','Gestion de versions',('git',),files=(('/etc/gitconfig','Configuration Git globale'),),docs='README.md'),
    Tool('python','00 · Fondations','Exécution et automatisation',('python3',)),
    Tool('ansible','00 · Fondations','Orchestration Ansible',('ansible-playbook',),files=((str(ROOT/'ansible/ansible.cfg'),'Configuration AUTOKOM'),),docs='docs/FOUNDATION.md'),
    Tool('terraform','00 · Fondations','Provisionnement IaC',('terraform',),docs='README.md'),
    Tool('nginx','01 · Réseau','Reverse proxy et TLS',('nginx',),'nginx',(
        ('/etc/nginx/nginx.conf','Configuration principale'),('/etc/nginx/sites-available','Virtual hosts disponibles'),('/etc/nginx/sites-enabled','Virtual hosts actifs'),('/var/log/nginx/access.log','Requêtes Nginx'),('/var/log/nginx/error.log','Erreurs Nginx'),(str(ROOT/'config/nginx/00-catchall.conf'),'Règles par défaut AUTOKOM')),docs='docs/FOUNDATION.md',critical=True),
    Tool('certbot','01 · Réseau','Certificats Let’s Encrypt',('certbot',),files=(('/etc/letsencrypt/renewal','Profils de renouvellement'),('/etc/letsencrypt/live','Certificats : accès restreint')),docs='docs/OBSERVABILITY.md'),
    Tool('ufw','01 · Réseau','Pare-feu hôte',('ufw',),'ufw', (('/etc/ufw','Règles du pare-feu'),),docs='docs/PHASE2-NETWORK-SECURITY.md',critical=True),
    Tool('docker','02 · Conteneurs','Moteur Docker',('docker',),'docker', (('/etc/docker/daemon.json','Paramètres du démon Docker'),('/var/lib/docker','Volumes, images et métadonnées : ne pas modifier manuellement')),docs='docs/FOUNDATION.md',critical=True),
    Tool('mysql','03 · Données','Serveur MySQL',('mysqld','mysql'),'mysql', (('/etc/mysql/my.cnf','Point d’entrée MySQL'),('/etc/mysql/mysql.conf.d/mysqld.cnf','Paramètres du serveur'),('/etc/mysql/mysql.conf.d/zz-autokom.cnf','Override AUTOKOM éventuel'),('/var/lib/mysql','Fichiers de base de données : ne pas modifier manuellement')),docs='docs/JENKINS-DATABASES.md',critical=True),
    Tool('postgresql','03 · Données','Serveur PostgreSQL',('psql',),'postgresql', (('/etc/postgresql','Clusters PostgreSQL et pg_hba.conf'),('/var/lib/postgresql','Données PostgreSQL : ne pas modifier manuellement')),docs='docs/JENKINS-DATABASES.md',critical=True),
    Tool('redis','03 · Données','Cache et messages',('redis-server',),'redis-server', (('/etc/redis/redis.conf','Configuration Redis'),('/var/lib/redis','Persistance Redis')),critical=True),
    Tool('jenkins','04 · CI/CD','Automatisation CI/CD',('jenkins',),'jenkins', (('/usr/lib/systemd/system/jenkins.service','Unité système (lecture seule)'),('/etc/systemd/system/jenkins.service.d','Overrides systemd'),('/var/lib/jenkins/config.xml','Configuration Jenkins (peut contenir des secrets)'),('/var/lib/jenkins/plugins','Plugins installés'),('/var/lib/jenkins/jobs','Définitions de jobs'),('/var/lib/jenkins/secrets','Secrets Jenkins : ne jamais publier'),(str(ROOT/'config/jenkins'),'Profils de plugins et Groovy AUTOKOM')),docs='docs/JENKINS-POSTINSTALL.md',critical=True),
    Tool('prometheus','05 · Observabilité','Collecte des métriques',('prometheus',),'prometheus', (('/etc/prometheus/prometheus.yml','Cibles et intervalles scrape'),('/etc/prometheus/rules/autokom-alerts.yml','Règles d’alertes liées'),('/etc/prometheus/alertmanager.yml','Configuration Alertmanager associée'),('/etc/nginx/.autokom-prometheus.htpasswd','Accès HTTP : hash restreint'),('/etc/systemd/system/prometheus.service.d','Overrides systemd AUTOKOM'),('/etc/autokom/observability.ini','Configuration dynamique AUTOKOM'),('/var/lib/prometheus','Données de séries temporelles'),('/etc/autokom/state/observability.json','État de configuration AUTOKOM'),('/var/log/nginx/access.log','Journal proxy (global)'),('/var/log/nginx/error.log','Erreurs proxy (global)')),docs='docs/OBSERVABILITY-CONFIG.md'),
    Tool('grafana','05 · Observabilité','Tableaux de bord',('grafana-server',),'grafana-server', (('/etc/grafana/grafana.ini','Paramètres Grafana'),('/etc/grafana/provisioning/datasources','Sources de données'),('/etc/grafana/provisioning/dashboards','Provisionnement des tableaux de bord'),('/etc/systemd/system/grafana-server.service.d','Overrides systemd AUTOKOM'),('/etc/autokom/observability.ini','Configuration dynamique AUTOKOM'),('/etc/grafana/provisioning/datasources/autokom-prometheus.yaml','Source Prometheus AUTOKOM'),('/etc/systemd/system/grafana-server.service.d/90-autokom.conf','Bind et domaine Grafana'),('/var/lib/grafana','Base de données locale et tableaux de bord')),docs='docs/OBSERVABILITY.md'),
    Tool('node-exporter','05 · Observabilité','Métriques de l’hôte',('prometheus-node-exporter',),'prometheus-node-exporter', (('/etc/default/prometheus-node-exporter','Variables service node exporter'),('/etc/systemd/system/prometheus-node-exporter.service.d/90-autokom.conf','Bind privé AUTOKOM')),docs='docs/OBSERVABILITY-OPERATIONS-v16.md'),
    Tool('alertmanager','05 · Observabilité','Routage des alertes',('prometheus-alertmanager',),'prometheus-alertmanager', (('/etc/prometheus/alertmanager.yml','Récepteurs et routage SMTP'),('/etc/prometheus/rules/autokom-alerts.yml','Règles d’alerte AUTOKOM'),('/etc/systemd/system/prometheus-alertmanager.service.d/90-autokom.conf','Bind local du service'),('/etc/prometheus/.autokom-smtp-password','Mot de passe SMTP : ne pas afficher')),docs='docs/OBSERVABILITY-OPERATIONS-v16.md'),
    # Stack de logs AUTOKOM : services Docker Compose, pas unités systemd.
    Tool('loki','06 · Journalisation','Stockage et recherche LogQL',files=(('/etc/autokom/logs/loki.yaml','Paramètres Loki'),('/etc/autokom/logs/compose.yaml','Orchestration Docker Compose'),('/var/lib/grafana/dashboards/autokom/autokom-logs.json','Tableau de bord des logs')),docs='docs/LOGGING-DUAL-BACKEND-v18.md'),
    Tool('alloy','06 · Journalisation','Collecteur de journaux Docker et système',files=(('/etc/autokom/logs/config.alloy','Pipeline Alloy'),('/etc/autokom/logs/compose.yaml','Orchestration Docker Compose')),docs='docs/LOGGING-DUAL-BACKEND-v18.md'),
    Tool('opensearch','06 · Journalisation','Moteur de recherche et indexation de logs',files=(('/etc/autokom/logs/compose.yaml','Orchestration Docker Compose'),('/etc/autokom/logs/.env','Secrets OpenSearch : accès restreint')),docs='docs/LOGGING-DUAL-BACKEND-v18.md'),
    Tool('data-prepper','06 · Journalisation','Pipeline OpenSearch de traitement des logs',files=(('/etc/autokom/logs/pipelines.yaml','Pipeline Data Prepper : contient des secrets'),('/etc/autokom/logs/compose.yaml','Orchestration Docker Compose')),docs='docs/LOGGING-DUAL-BACKEND-v18.md'),
    Tool('opensearch-dashboards','06 · Journalisation','Interface de recherche de logs OpenSearch',files=(('/etc/autokom/logs/compose.yaml','Orchestration Docker Compose'),),docs='docs/LOGGING-DUAL-BACKEND-v18.md'),
    Tool('trivy','06 · Sécurité','Scan images et dépendances',('trivy',),docs='docs/FOUNDATION.md'),
    Tool('semgrep','06 · Sécurité','Analyse de code statique',('semgrep',),docs='docs/FOUNDATION.md'),
    Tool('nikto','06 · Sécurité','Audit web',('nikto',),docs='docs/FOUNDATION.md'),
    Tool('wazuh-manager','06 · Sécurité','Collecte et détection Wazuh',(),'wazuh-manager', (('/var/ossec/etc/ossec.conf','Configuration manager Wazuh'),('/var/ossec/logs','Journaux Wazuh')),docs='README.md',critical=True),
    Tool('wazuh-indexer','06 · Sécurité','Moteur d’indexation Wazuh',(),'wazuh-indexer', (('/etc/wazuh-indexer/opensearch.yml','Paramètres indexeur'),),critical=True),
    Tool('wazuh-dashboard','06 · Sécurité','Interface Wazuh',(),'wazuh-dashboard', (('/etc/wazuh-dashboard/opensearch_dashboards.yml','Paramètres dashboard'),)),
    Tool('elasticsearch','06 · Sécurité','Indexation et recherche',('elasticsearch',),'elasticsearch', (('/etc/elasticsearch/elasticsearch.yml','Paramètres Elasticsearch'),),critical=True),
    Tool('kibana','06 · Sécurité','Exploration de journaux',('kibana',),'kibana', (('/etc/kibana/kibana.yml','Configuration Kibana'),)),
)
TOOLS = {x.name:x for x in SPECS}


def get(name):
    if name not in TOOLS:
        raise ToolError(f'Outil inconnu : {name}. Utiliser autokom tools list --all')
    return TOOLS[name]


def proc(args, timeout=7):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


LOG_DOCKER_SERVICES = frozenset(('loki', 'alloy', 'opensearch',
                                 'data-prepper', 'opensearch-dashboards'))


def logging_docker_state(name):
    """Read-only container state; no docker compose `up`, no secret output.

    Rely on Compose's service/project labels, not guessed container names.
    Distinguish absent, stopped and running without requiring a systemd unit.
    """
    if name not in LOG_DOCKER_SERVICES:
        return 'absent'
    if not shutil.which('docker'):
        return 'absent'
    p = proc(['docker', 'ps', '--all',
              '--filter', 'label=com.docker.compose.project=autokom-logs',
              '--filter', f'label=com.docker.compose.service={name}',
              '--format', '{{.State}}'],timeout=6)
    if p is None or p.returncode:
        return 'indisponible'
    states = p.stdout.strip().splitlines()
    if not states:
        return 'absent'
    return 'active' if 'running' in states else 'inactive'


def service_state(t):
    if t.name in LOG_DOCKER_SERVICES:
        return logging_docker_state(t.name)
    if t.name == 'ufw':
        result=proc(['ufw','status'])
        if not result or result.returncode:
            return 'indisponible'
        return 'active' if result.stdout.lower().startswith('status: active') else 'inactive'
    if not t.unit:
        return 'outil CLI' if any(shutil.which(c) for c in t.binaries) else 'absent'
    if not Path('/run/systemd/system').is_dir():
        return 'indisponible' if any(shutil.which(c) for c in t.binaries) else 'absent'
    loaded = proc(['systemctl','show',t.unit,'--property=LoadState','--value'])
    if not loaded or loaded.returncode or loaded.stdout.strip() in ('not-found',''):
        return 'absent'
    active = proc(['systemctl','is-active',t.unit])
    return active.stdout.strip() if active and active.stdout.strip() else 'inconnu'


def say(text):
    print(colored_marker(text))


def list_tools(*, all_tools=False):
    """Inventaire lisible, par catégorie, sans changer les autres vues CLI.

    Afficher seulement les outils détectés par défaut ; --all affiche aussi
    les absents. Les tables sont en lecture seule, avec largeur adaptative.
    """
    print('  ◈ AUTOKOM / OUTILS & SERVICES')
    groups = {}
    for t in SPECS:
        state = service_state(t)
        if not all_tools and state == 'absent':
            continue
        groups.setdefault(t.group, []).append((
            Cell(t.name, 'info'), _state_cell(state), t.info,
        ))
    if not groups:
        say('  − Aucun outil détecté ; utiliser autokom tools list --all')
    for group, rows in groups.items():
        print()
        say('  › ' + group)
        table(['Outil', 'État', 'Fonction'], rows, row_dividers=False)
    print('\n  › autokom tools show jenkins · autokom tools list --all')


def _state_cell(state):
    if state in ('active', 'outil CLI'):
        return Cell('✓ ' + state, 'success')
    if state in ('inactive', 'failed', 'activating', 'deactivating'):
        return Cell('! ' + state, 'warning')
    return Cell('− ' + state, 'muted')


def discovered_service_files(name):
    """Files really present for a service, including AUTOKOM Nginx vhosts.

    Discover only small, readable Nginx config files; never crawl TSDB/data,
    follow secret symlinks, or read certificate private keys.
    """
    if name not in ('prometheus','grafana','jenkins','nginx','alertmanager','node-exporter'):
        return []
    out=[]
    paths=[]
    for root in (Path('/etc/nginx/sites-available'),Path('/etc/nginx/sites-enabled'),Path('/etc/nginx/conf.d')):
        if root.is_dir():
            paths.extend(sorted(root.glob('*.conf'))[:200])
    domains={'prometheus':'prometheus','grafana':'grafana','jenkins':'jenkins'}
    for candidate in paths:
        try:
            # Resolve only under /etc/nginx, rejecting arbitrary symlink targets.
            actual=candidate.resolve(strict=True)
            if not actual.is_relative_to(Path('/etc/nginx')) or not actual.is_file():
                continue
            if actual.stat().st_size>512_000:
                continue
            if name != 'nginx':
                content=actual.read_text(encoding='utf-8')
                markers={'prometheus':('autokom-prometheus','127.0.0.1:9090','autokom_prometheus'),
                         'grafana':('autokom-grafana','127.0.0.1:3000','autokom_grafana'),
                         'jenkins':('jenkins','127.0.0.1:8080'),
                         'alertmanager':('alertmanager','127.0.0.1:9093'),
                         'node-exporter':('node-exporter','127.0.0.1:9100')}
                if not any(m in str(candidate).lower() or m in content.lower() for m in markers[name]):
                    continue
            out.append((str(candidate), 'Virtual host / upstream Nginx' + (' (actif)' if 'sites-enabled' in candidate.parts else '')))
        except (OSError,UnicodeError):
            continue
    # Include systemd's actual unit and environment files; these paths are
    # read as metadata, not dereferenced, and editing is separately restricted.
    t=get(name)
    if t.unit:
        for key, label in (('FragmentPath','Unité systemd chargée'),('DropInPaths','Overrides systemd actifs'),
                           ('EnvironmentFiles','Fichiers environnement déclarés')):
            p=proc(['systemctl','show',t.unit,'--property='+key,'--value'])
            if p and p.returncode==0:
                for match in re.findall(r'/[^\s()]+',p.stdout):
                    path=match.rstrip(';')
                    if path.startswith(('/etc/','/usr/lib/systemd/','/lib/systemd/','/opt/autokom/')):
                        out.append((path,label))
    unique={}
    for key,desc in out:
        unique.setdefault(key,desc)
    return list(unique.items())


def show(name):
    t=get(name)
    state=service_state(t)
    ui_section(t.name, f'{t.group} · {t.info}', tone=name if name in ('jenkins','prometheus','grafana') else None)
    overview=[('Service', t.name), ('État', _state_cell(state)),
              ('Catégorie', t.group), ('Utilité', t.info)]
    if t.unit:
        overview.append(('Unité systemd', t.unit + '.service'))
    table(['Propriété', 'Valeur'], overview)

    print('\n  CONFIGURATION ET DONNÉES')
    files=[]
    seen_paths=set()
    for path, desc in (*t.files, *discovered_service_files(name)):
        full=str(Path(path).absolute())
        if full in seen_paths:
            continue
        seen_paths.add(full)
        present=Path(full).exists()
        files.append((Cell('✓ présent','success') if present else Cell('− absent','muted'), full, desc))
    if files:
        table(['Présence', 'Chemin absolu', 'Rôle / explication'], files)
    else:
        print('  − Pas de fichier propre à cet outil dans le catalogue')

    if t.unit:
        effective=[]
        for prop,label in (('FragmentPath','Fichier unité actif'),
                           ('DropInPaths','Overrides chargés'),
                           ('EnvironmentFiles','Fichiers d’environnement déclarés'),
                           ('WorkingDirectory','Répertoire de travail')):
            rec=proc(['systemctl','show',t.unit,'--property='+prop,'--value'])
            if rec and rec.returncode==0 and rec.stdout.strip() not in ('','n/a'):
                effective.append((label, rec.stdout.strip()))
        if effective:
            print('\n  ENVIRONNEMENT SYSTEMD · MÉTADONNÉES')
            table(['Élément', 'Chemin / valeur déclarée'], effective)
    print('\n  RÉFÉRENCES ET ACTIONS')
    refs=[]
    if t.docs: refs.append(('Guide AUTOKOM', str(ROOT / t.docs)))
    if t.unit:
        refs.extend((('Unité et overrides','systemctl cat ' + t.unit),
                     ('Paramètres systemd', 'systemctl show '+ t.unit)))
    refs.append(('Variables · noms','autokom services show '+name+' -e'))
    refs.append(('Variables · valeurs', 'sudo autokom services show '+name+' -E'))
    refs.append(('Configurer', 'sudo autokom services configure '+name))
    refs.append(('Fichiers et alias',f'autokom tools {name} files'))
    refs.append(('Lire un fichier',f'autokom tools {name} cat ALIAS'))
    refs.append(('Éditer un fichier',f'sudo autokom tools {name} nano ALIAS'))
    refs.append(('Modifier environnement',f'sudo autokom tools {name} env set VARIABLE'))
    table(['Action', 'Chemin ou commande'], refs)


def _parse_env_lines(lines):
    """Retourne nom -> valeur depuis lignes NUL ou KEY=VALUE systemd."""
    pairs = {}
    for item in lines:
        if '=' in item:
            key,value=item.split('=',1)
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):
                pairs[key]=value
    return pairs


def effective_env(t, *, runtime=True):
    if not t.unit:
        return {}
    found={}
    res=proc(['systemctl','show',t.unit,'--property=Environment','--value'])
    if res and res.returncode==0:
        try:
            found.update(_parse_env_lines(shlex.split(res.stdout)))
        except ValueError:
            pass
    # EnvironmentFile expansion is not necessarily part of systemctl's
    # Environment property. Runtime process env is authoritative if readable.
    if runtime:
        pidproc=proc(['systemctl','show',t.unit,'--property=MainPID','--value'])
        try: pid=int(pidproc.stdout.strip()) if pidproc else 0
        except ValueError: pid=0
        if pid>1:
            try:
                envdata=Path(f'/proc/{pid}/environ').read_bytes()
                found.update(_parse_env_lines(x.decode('utf-8','replace') for x in envdata.split(b'\0') if x))
            except (PermissionError,OSError):
                pass
    # Include staged AUTOKOM EnvironmentFile keys, even before service restart.
    # Runtime variables take precedence; an edit may still require restart.
    try:
        from tool_files import managed_env,ENV_TOOLS
        if t.name in ENV_TOOLS:
            staged=managed_env(t.name)
            found={**staged,**found}
    except (OSError,ValueError):
        pass
    return dict(sorted(found.items()))


def _tty_challenge(label, required):
    """Read a single confirmation via /dev/tty without seekable r+ streams."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ToolError('Confirmation : terminal interactif requis (sortie redirigée refusée)')
    fd = None
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        os.write(fd, f'  ! {label}. Taper {required} : '.encode('utf-8'))
        buf=bytearray()
        while len(buf) < 128:
            ch=os.read(fd, 1)
            if not ch or ch in (b'\n',b'\r'):
                break
            buf.extend(ch)
        return buf.decode('utf-8','replace').strip() == required
    except OSError as e:
        raise ToolError('Impossible de lire le terminal de confirmation') from e
    finally:
        if fd is not None: os.close(fd)


def confirm_sensitive(prompt='AFFICHER-SECRETS'):
    return _tty_challenge('La sortie peut contenir tokens, mots de passe et clés', prompt)


def environments(name, *, values=False):
    t=get(name)
    if not t.unit:
        raise ToolError(f'{name} ne possède pas de service systemd propre')
    if values:
        if os.geteuid()!=0:
            raise ToolError('Valeurs réelles : utiliser sudo autokom tools show '+name+' -E')
        if not confirm_sensitive():
            raise ToolError('Affichage des secrets annulé')
    env=effective_env(t)
    ui_section(f'{name} · Variables', 'VALEURS SENSIBLES' if values else 'Noms seulement · valeurs masquées',
            tone=name if name in ('jenkins','prometheus','grafana') else None)
    if not env:
        print('  − Aucune variable récupérable ; consulter EnvironmentFiles via tools show.')
        return
    table(['Variable', 'Valeur' if values else 'Visibilité'],
          [(Cell(key,'info'), Cell(val,'warning') if values else Cell('masquée','muted'))
           for key,val in env.items()])
    if values:
        print('  ! Données sensibles : ne pas copier cette sortie dans un ticket, chat ou dépôt Git.')


def require_root():
    if os.geteuid()!=0:
        raise ToolError('Cette opération nécessite sudo')


def confirm_action(verb, name):
    return _tty_challenge(f'{verb} {name} peut interrompre les applications', name)


def service_action(name, verb):
    t=get(name); require_root()
    if not t.unit:
        raise ToolError(f'{name} n’est pas un service systemd')
    if verb not in ('restart','stop'):
        raise ToolError('Action inconnue')
    if name == 'ufw':
        raise ToolError('UFW ne peut pas être stoppé/redémarré ici : préserver les protections réseau et SSH')
    if service_state(t)=='absent':
        raise ToolError(f'Service {name} absent')
    if not confirm_action(verb, name):
        raise ToolError('Opération annulée')
    result=proc(['systemctl',verb,t.unit],timeout=120)
    if result is None or result.returncode:
        raise ToolError(f'{verb} {name} a échoué : consulter journalctl -u {t.unit}')
    after=service_state(t)
    desired='active' if verb=='restart' else 'inactive'
    if after!=desired:
        raise ToolError(f'{name} : état {after}, attendu {desired}. Consulter journalctl -u {t.unit}')
    print(f'  ✓ {name} : {verb} confirmé ({after})')


def configure(name):
    get(name); require_root()
    modules={'jenkins':('jenkins.sh',True), 'prometheus':('observability.sh',True),
             'grafana':('observability.sh',True),'mysql':('services.sh',False),
             'postgresql':('services.sh',False)}
    if name in ('mysql','postgresql'):
        raise ToolError('Les bases existantes ne seront pas reconfigurées via un assistant Jenkins : consulter autokom tools show '+name)
    if name not in modules:
        raise ToolError('Configuration automatique indisponible pour '+name+' ; voir autokom tools show '+name)
    script,interactive=modules[name]
    path=ROOT/'scripts/install'/script
    args=['bash',str(path),'--apply'] + (['--interactive'] if interactive else [])
    rc=subprocess.call(args)
    if rc:
        raise ToolError(f'Configuration {name} interrompue (code {rc})')


def change(name, updates, *, apply=False):
    """Update ONLY AUTOKOM's secret-free INI, not arbitrary vendor configs."""
    t=get(name)
    if name not in ('jenkins','prometheus','grafana'):
        raise ToolError('Changement automatisé seulement pour Jenkins/Prometheus/Grafana ; voir tools show')
    if not updates:
        show(name)
        print('  › Exemple : sudo autokom tools change prometheus port=9091 --apply')
        return
    pairs=[]
    for update in updates:
        if '=' not in update:
            raise ToolError('Attendu : CLE=VALEUR')
        key,val=update.split('=',1)
        if key not in ALLOWED[name]:
            raise ToolError(f'Option {name}.{key} inconnue ou non modifiable')
        # Jenkins actual listening port is externally managed by its systemd
        # unit. Never pretend that changing the observability INI changes it.
        if name=='jenkins' and key in ('port','host'):
            raise ToolError('Port/adresse Jenkins : gérer systemd et le proxy via configure Jenkins, pas le fichier observabilité seul')
        pairs.append(f'{name}.{key}={val}')
    # validation of full merged config (including port collisions)
    settings=load_observability(changes=pairs)
    print('  ◈ CHANGEMENTS PROPOSÉS (non sensibles)')
    for pair in pairs: print('  + '+pair)
    if not apply:
        print('  › Simulation : ajouter --apply pour enregistrer. Aucun service redémarré.')
        return
    require_root()
    if not confirm_action('Modifier la configuration AUTOKOM de',name):
        raise ToolError('Changement annulé')
    path=Path('/etc/autokom/observability.ini')
    if path.is_symlink():
        raise ToolError('Fichier de configuration symbolique interdit')
    ini=ConfigParser(interpolation=None)
    if path.exists():
        ini.read(path,encoding='utf-8')
    for pair in pairs:
        key,value=pair.split('=',1)
        section,option=key.split('.',1)
        if not ini.has_section(section): ini.add_section(section)
        ini.set(section,option,value)
    import io
    buff=io.StringIO();ini.write(buff)
    new=buff.getvalue()
    path.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    old=path.read_text(encoding='utf-8') if path.exists() else None
    # Read-only previous config is backed up before replacement
    import tempfile
    tmp=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(path.parent),prefix='.autokom-',delete=False) as fh:
            tmp=Path(fh.name); os.chmod(tmp,0o600);fh.write(new)
        # verify actual tempfile using alternate system_path
        load_observability(system_path=tmp)
        if old is not None:
            backup=path.with_suffix('.ini.bak')
            if not backup.exists():
                with open(backup,'x',encoding='utf-8') as fh:
                    os.chmod(backup,0o600);fh.write(old)
        os.replace(tmp,path)
    finally:
        if tmp and tmp.exists():tmp.unlink()
    print('  ✓ Configuration enregistrée : '+str(path))
    print('  ! Aucun redémarrage automatique. Vérifier, puis lancer sudo autokom install.')


def dispatch(args):
    verb=args.tool_cmd
    if not verb:
        print('  Utiliser autokom tools -h pour découvrir les commandes.');return 0
    if verb in TOOLS:
        from tool_files import dispatch_tool_files
        dispatch_tool_files(verb,args.file_action,args.file_target,args.file_extra)
        return 0
    if verb=='list':list_tools(all_tools=args.all);return 0
    if verb=='show':
        if args.all:
            if args.tool_name:
                raise ToolError('`show --all` ne prend pas de nom ; utiliser `show NOM` pour un outil')
            list_tools(all_tools=True)
            return 0
        if not args.tool_name:
            raise ToolError('Spécifier un outil (ex. `show loki`) ou `show --all` pour le catalogue')
        if args.environments or args.environments_values:
            environments(args.tool_name,values=args.environments_values)
        else:show(args.tool_name)
        return 0
    if verb in ('restart','stop'):
        service_action(args.tool_name,verb);return 0
    if verb=='configure':configure(args.tool_name);return 0
    if verb=='change':change(args.tool_name,args.settings,apply=args.apply);return 0
    raise ToolError('Opération inconnue')
