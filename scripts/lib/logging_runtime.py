#!/usr/bin/env python3
"""AUTOKOM logging runtime: one active owner for Loki+Alloy (Docker or systemd).

Native mode is intentionally Loki-only. OpenSearch/Data Prepper remain a distinct
backend and cannot be silently destroyed or severed during a runtime switch.
A mode change is transactional for running services and preserves a durable
backup before removing the former installation. The previous mode is restored
when a pre-cleanup health or canary check fails.
"""
from __future__ import annotations
from cli_colors import pretty_print as print
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import datetime
import urllib.request

NATIVE = Path('/etc/autokom/logs-native')
STATE = Path('/etc/autokom/state/logs-runtime.json')
DATA = Path('/var/lib/autokom/loki')
BACKUP_ROOT = Path('/var/backups/autokom/logs')
UNITS = ('loki','alloy')

class RuntimeErrorSafe(RuntimeError): pass


def cmd(*args, timeout=180, check=True):
    try:
        from tool_banner import Activity
        label=' '.join(str(x) for x in args[:2])
        with Activity('Exécution : '+label):
            p=subprocess.run(list(args),capture_output=True,text=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeErrorSafe('Commande indisponible ou expirée: '+str(args[0])) from exc
    if check and p.returncode:
        raise RuntimeErrorSafe('Échec '+ ' '.join(args[:3]) +f' (code {p.returncode}); examiner les journaux locaux')
    return p


def active_native():
    return any(cmd('systemctl','is-active','--quiet',name,check=False).returncode == 0 for name in UNITS)


def active_docker():
    if not shutil.which('docker'):return False
    try:
        p=cmd('docker','ps','--filter','label=com.docker.compose.project=autokom-logs',
              '--filter','label=com.docker.compose.service=loki','--format','{{.ID}}',check=False)
        q=cmd('docker','ps','--filter','label=com.docker.compose.project=autokom-logs',
              '--filter','label=com.docker.compose.service=alloy','--format','{{.ID}}',check=False)
    except RuntimeErrorSafe:
        return False
    return p.returncode == 0 and q.returncode == 0 and bool(p.stdout.strip() or q.stdout.strip())


def detected():
    native=active_native()
    docker=active_docker()
    if native and docker:return 'mixed'
    if native:return 'systemd'
    if docker:return 'docker'
    try:
        value=json.loads(STATE.read_text())['runtime']
        if value in ('systemd','docker'): return value
    except (FileNotFoundError,OSError,KeyError,ValueError,TypeError):pass
    return 'none'


def native_ready():
    if not all(cmd('systemctl','is-active','--quiet',unit,check=False).returncode==0 for unit in UNITS):
        return False
    from logging_health import http_status
    return all(http_status(name)['ok'] for name in UNITS)


def runtime_ready(backend, runtime):
    if runtime=='systemd':return backend=='loki' and native_ready() and not active_docker()
    from logging_stack import ready
    return ready(backend) and not active_native()


def report():
    choice=detected()
    print('  ◈ AUTOKOM / MODE D’EXÉCUTION LOKI + ALLOY')
    print('  › Mode détecté : '+choice)
    print('  › Docker Compose : '+('actif' if active_docker() else 'inactif'))
    print('  › Services systemd : '+('actifs' if active_native() else 'inactifs'))
    if choice=='mixed': print('  ✗ Deux modes simultanément actifs : corriger le conflit avant installation.')
    print('  › Ports privés : Loki 127.0.0.1:3100 ; Alloy 127.0.0.1:12345')


def plan(target):
    if target not in ('docker','systemd'): raise RuntimeErrorSafe('Mode invalide: '+target)
    current=detected()
    print(f'  AUTOKOM LOGS / PLAN — {current} → {target} (sans modification)')
    if current=='mixed': print('  ✗ État mixte : intervention nécessaire')
    elif current==target: print('  ✓ Mode sélectionné ; contrôle de santé et réconciliation requis')
    elif current=='none': print('  + Installer Loki/Alloy en mode '+target)
    else:
        print('  1. Préparer configuration et binaires du nouveau mode')
        print('  2. Sauvegarder la configuration et toutes les données Loki existantes')
        print('  3. Arrêter uniquement l’ancien couple Loki/Alloy')
        print('  4. Transférer les données, démarrer le nouveau couple et vérifier les endpoints')
        print('  5. Vérifier une vraie ingestion journald → Alloy → Loki')
        print('  6. Supprimer les composants de l’ancien mode après preuve de conformité')
        print('  ! Interruption temporaire des logs possible pendant la bascule')
    print('  › Aucun autre conteneur Docker, service Jenkins ou Grafana n’est ciblé.')


def secure_save(path, data, mode=0o644):
    if path.is_symlink():raise RuntimeErrorSafe('Lien symbolique refusé : '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.autokom-')
    try:
        with os.fdopen(fd,'w') as f:f.write(data)
        os.chmod(temp,mode)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def save_state(mode):
    secure_save(STATE,json.dumps({'schema':1,'runtime':mode},indent=2)+'\n',0o644)


def verify_canary():
    from logs_canary import probe
    if probe(wait=45) != 0:
        raise RuntimeErrorSafe('Sonde E2E en échec ; ancien mode conservé et retour arrière nécessaire')


def require_loki_only(backend):
    if backend != 'loki':
        raise RuntimeErrorSafe('Mode systemd : Loki+Alloy seulement. OpenSearch/dual reste en mode Docker, sans migration destructive implicite.')
    if shutil.which('docker'):
        p=cmd('docker','ps','-a','--filter','label=com.docker.compose.project=autokom-logs',
              '--filter','label=com.docker.compose.service=opensearch','--format','{{.ID}}',check=False)
        if p.returncode==0 and p.stdout.strip():
            raise RuntimeErrorSafe('OpenSearch actif dans le projet Compose : transition systemd bloquée ; aucune suppression automatique.')


def new_backup():
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    BACKUP_ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(BACKUP_ROOT,0o700)
    result=BACKUP_ROOT/stamp
    result.mkdir(mode=0o700)
    return result


def backup_configs(target):
    for directory in ('/etc/autokom/logs','/etc/autokom/logs-native'):
        source=Path(directory)
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source,target/source.name,symlinks=True)
    return target


def docker_container(service):
    p=cmd('docker','ps','-a','--filter','label=com.docker.compose.project=autokom-logs',
          '--filter',f'label=com.docker.compose.service={service}','--format','{{.ID}}')
    ids=p.stdout.splitlines()
    if len(ids)!=1:raise RuntimeErrorSafe('Conteneur Compose manquant ou ambigü : '+service)
    return ids[0]


def docker_compose(*args):
    from logging_stack import docker
    return cmd(*docker('--profile','loki',*args),timeout=600)


def native_files():
    from logging_stack import yaml_loki, alloy_config
    loki=yaml_loki().replace('http_listen_port: 3100','http_listen_address: 127.0.0.1\n  http_listen_port: 3100').replace('/loki/',str(DATA)+'/').replace('path_prefix: /loki',f'path_prefix: {DATA}')
    alloy=alloy_config('loki').replace('http://loki:3100/loki/api/v1/push','http://127.0.0.1:3100/loki/api/v1/push')
    return {'loki.yaml':loki,'config.alloy':alloy}


def install_vendor_packages():
    # APT packages are official; never use the Ubuntu `loki` package implicitly.
    key=Path('/etc/apt/keyrings/grafana.asc')
    if not key.exists():
        key.parent.mkdir(parents=True,exist_ok=True)
        cmd('curl','-fsSL','--output',str(key),'https://apt.grafana.com/gpg-full.key')
        os.chmod(key,0o644)
    source=Path('/etc/apt/sources.list.d/grafana.list')
    expected='deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main\n'
    if source.exists() and source.read_text()!=expected:
        raise RuntimeErrorSafe('Dépôt Grafana existant modifié : intervention nécessaire')
    if not source.exists():secure_save(source,expected)
    # Stay on the same exact versions as the Docker stack during TSDB migration.
    # Some package repositories drop historical versions; abort rather than
    # silently upgrade on an irreversible storage-format transition.
    cmd('apt-get','update',timeout=240)
    pins={}
    for package,wanted in (('loki','3.7.0'),('alloy','1.11.0')):
        versions=cmd('apt-cache','madison',package).stdout
        found=[line.split('|')[1].strip() for line in versions.splitlines()
               if len(line.split('|'))>=3 and
               (line.split('|')[1].strip()==wanted or line.split('|')[1].strip().startswith(wanted+'-'))]
        if not found:
            raise RuntimeErrorSafe(f'Version {package}={wanted} introuvable dans le dépôt Grafana ; aucune bascule engagée')
        pins[package]=found[0]
    # Runtime mask prevents post-install starting native daemons before cutover.
    for unit in UNITS:
        cmd('systemctl','mask','--runtime',unit+'.service',check=False)
    try:
        cmd('apt-get','install','-y','--no-install-recommends',
            'loki='+pins['loki'],'alloy='+pins['alloy'],timeout=600)
    finally:
        for unit in UNITS:cmd('systemctl','unmask','--runtime',unit+'.service',check=False)
    if not shutil.which('loki') or not shutil.which('alloy'):
        raise RuntimeErrorSafe('Binaires Loki/Alloy officiels introuvables après installation')


def prepare_native():
    NATIVE.mkdir(parents=True,exist_ok=True,mode=0o755)
    expected=native_files()
    for key,content in expected.items():
        path=NATIVE/key
        if path.exists() and path.read_text()!=content:
            raise RuntimeErrorSafe('Fichier natif modifié manuellement : '+str(path))
        if not path.exists():secure_save(path,content)
    if not cmd('id','-u','autokom-loki',check=False).returncode==0:
        cmd('useradd','--system','--home-dir',str(DATA),'--shell','/usr/sbin/nologin','autokom-loki')
    DATA.mkdir(parents=True,exist_ok=True)
    os.chmod(DATA,0o750)
    # Package service usually runs as `alloy`; ensure reading journald & nginx.
    if cmd('id','-u','alloy',check=False).returncode!=0:
        raise RuntimeErrorSafe('Utilisateur alloy manquant après installation APT')
    groups=[]
    for group in ('adm','systemd-journal','docker'):
        if cmd('getent','group',group,check=False).returncode==0:groups.append(group)
    if groups:cmd('usermod','-aG',','.join(groups),'alloy')
    print('  ! Le groupe docker donne un accès très privilégié : une politique de socket-proxy reste recommandée.')
    native_loki=NATIVE/'loki.yaml'
    native_alloy=NATIVE/'config.alloy'
    unit_loki=Path('/etc/systemd/system/loki.service.d/90-autokom.conf')
    unit_alloy=Path('/etc/systemd/system/alloy.service.d/90-autokom.conf')
    loki_binary=shutil.which('loki')
    secure_save(unit_loki,'[Service]\nExecStart=\nExecStart='+loki_binary+' -config.file='+str(native_loki)+'\nUser=autokom-loki\nGroup=autokom-loki\nNoNewPrivileges=true\n')
    secure_save(NATIVE/'alloy.env','CONFIG_FILE="'+str(native_alloy)+'"\nCUSTOM_ARGS="--server.http.listen-addr=127.0.0.1:12345"\n')
    secure_save(unit_alloy,'[Service]\nEnvironmentFile=\nEnvironmentFile='+str(NATIVE/'alloy.env')+'\nNoNewPrivileges=true\n')
    cmd('systemctl','daemon-reload')
    cmd(shutil.which('alloy'),'validate',str(native_alloy),timeout=90)
    secure_save(NATIVE/'.autokom-installed','loki=3.7.0\nalloy=1.11.0\n',0o600)
    # Loki's `-verify-config` is not available in all package versions.
    print('  ✓ Fichiers natifs et validation Alloy préparés')


def chown_loki(root):
    import pwd
    id=pwd.getpwnam('autokom-loki')
    for folder,dirs,files in os.walk(root,followlinks=False):
        os.chown(folder,id.pw_uid,id.pw_gid)
        for name in dirs+files:
            path=Path(folder)/name
            if not path.is_symlink():os.chown(path,id.pw_uid,id.pw_gid)


def retire_managed_docker_configs():
    # Only uninstall generated files matching AUTOKOM's manifest; never touch
    # an operator-modified file. Originals are already inside the backup.
    from logging_stack import STATE_DIR, MANIFEST, read_manifest, sha
    manifest=read_manifest()
    for name in ('compose.yaml','config.alloy','loki.yaml','pipelines.yaml'):
        path=STATE_DIR/name
        if path.is_symlink():raise RuntimeErrorSafe('Fichier Docker symbolique : retrait refusé')
        if path.exists() and manifest.get(name)!=sha(path.read_text()):
            raise RuntimeErrorSafe('Configuration Docker non gérée : retrait manuel nécessaire : '+str(path))
    for name in ('compose.yaml','config.alloy','loki.yaml','pipelines.yaml',MANIFEST,'.env'):
        path=STATE_DIR/name
        if path.is_file() and not path.is_symlink():path.unlink()


def docker_volume_path():
    from logging_stack import STATE_DIR
    volume='autokom-logs_loki-data'
    out=cmd('docker','volume','inspect',volume,'--format','{{json .}}')
    record=json.loads(out.stdout)
    if record.get('Driver')!='local' or (record.get('Labels') or {}).get('com.docker.compose.project')!='autokom-logs':
        raise RuntimeErrorSafe('Volume Loki non local/non géré : copie automatique interdite')
    mount=Path(record['Mountpoint'])
    root=cmd('docker','info','--format','{{.DockerRootDir}}').stdout.strip()
    if not mount.is_dir() or mount.is_symlink() or not mount.resolve().is_relative_to(Path(root).resolve()):
        raise RuntimeErrorSafe('Chemin volume Docker invalide')
    return mount


def wait_ready():
    from logging_health import http_status
    from tool_banner import Activity
    until=time.monotonic()+75
    with Activity('Attente de disponibilité Loki et Alloy'):
        while time.monotonic()<until:
            if all(http_status(item)['ok'] for item in UNITS):return
            time.sleep(3)
    raise RuntimeErrorSafe('Endpoints Loki/Alloy inaccessibles à 127.0.0.1; aucune suppression lancée')


def require_confirm(source,target):
    from menu import pick
    try:
        selected=pick(f'AUTOKOM · Confirmer {source} → {target}',
           ['Confirmer la migration, puis le retrait de l’ancien mode'],default=1,
           cancel_label='Annuler sans modifier l’infrastructure')
    except RuntimeError as exc:
        raise RuntimeErrorSafe('Migration destructive : confirmation interactive TTY obligatoire') from exc
    if selected!=1:raise RuntimeErrorSafe('Migration annulée par l’administrateur')


def migrate_to_native(backend):
    require_loki_only(backend)
    previous=detected()
    if previous=='mixed':raise RuntimeErrorSafe('Modes Docker et systemd simultanés : intervention manuelle')
    if previous=='systemd':
        if not native_ready():raise RuntimeErrorSafe('Services systemd déjà présents mais indisponibles : diagnostic nécessaire')
        from logging_stack import provision_grafana
        from logging_monitoring import apply as apply_monitoring
        changed=provision_grafana();apply_monitoring()
        from logging_stack import sync_grafana_after_install
        sync_grafana_after_install(changed)
        return
    # Pre-existing unmanaged packages must not be overwritten by AUTOKOM.
    if previous!='systemd' and (shutil.which('loki') or shutil.which('alloy')) and             not (NATIVE/'.autokom-installed').is_file():
        raise RuntimeErrorSafe('Installation native préexistante non gérée : refus de modifier les services')
    require_confirm(previous,'systemd') if previous!='none' else None
    if previous=='docker':
        from logging_stack import validate_managed, read_manifest
        validate_managed(read_manifest())
    backup=new_backup()
    backup_configs(backup)
    install_vendor_packages()
    prepare_native()
    if previous=='docker':
        # Stop before copying Loki TSDB to avoid inconsistent filesystem state.
        docker_compose('stop','alloy','loki')
        try:
            old=docker_container('loki')
            store=backup/'loki-data'
            store.mkdir()
            cmd('docker','cp',old+':/loki/.',str(store),timeout=600)
            if any(DATA.iterdir()):raise RuntimeErrorSafe('Données natives déjà présentes : fusion automatique interdite')
            from tool_banner import Activity
            with Activity('Transfert sécurisé des données Loki'):
                shutil.copytree(store,DATA,dirs_exist_ok=True)
        except Exception:
            docker_compose('up','-d','--no-build','alloy','loki')
            raise
    try:
        chown_loki(DATA)
        for unit in UNITS:cmd('systemctl','enable','--now',unit)
        wait_ready()
        verify_canary()
    except Exception:
        for unit in UNITS:cmd('systemctl','disable','--now',unit,check=False)
        if previous=='docker':docker_compose('up','-d','--no-build','alloy','loki')
        raise
    save_state('systemd')
    if previous=='docker':
        docker_compose('rm','-s','-f','alloy','loki')
        remaining_volumes=[]
        for volume in ('autokom-logs_loki-data','autokom-logs_alloy-data'):
            removed=cmd('docker','volume','rm',volume,check=False)
            if removed.returncode: remaining_volumes.append(volume)
        from logging_stack import IMAGES
        for item in ('loki','alloy'):
            cmd('docker','image','rm',IMAGES[item],check=False)
        retire_managed_docker_configs()
        if remaining_volumes:
            raise RuntimeErrorSafe('Mode natif vérifié, mais nettoyage Docker incomplet : '+', '.join(remaining_volumes)+' ; ne pas déclarer la migration terminée')
        print('  ✓ Ancien couple Loki/Alloy Docker retiré ; autres conteneurs inchangés')
    from logging_stack import provision_grafana
    from logging_monitoring import apply as apply_monitoring
    changed=provision_grafana();apply_monitoring()
    from logging_stack import sync_grafana_after_install
    sync_grafana_after_install(changed)
    print('  ✓ Loki/Alloy natifs validés ; sauvegarde conservée : '+str(backup))


def migrate_to_docker(backend):
    from logging_stack import apply_docker
    if backend!='loki':raise RuntimeErrorSafe('Migration systemd vers Docker : backend loki seulement')
    previous=detected()
    if previous=='mixed':raise RuntimeErrorSafe('Modes concurrents détectés : intervention nécessaire')
    if previous=='docker':return apply_docker(backend)
    if previous=='none':return apply_docker(backend)
    if not (NATIVE/'.autokom-installed').is_file():
        raise RuntimeErrorSafe('Services natifs non gérés par AUTOKOM : purge automatique interdite')
    require_confirm(previous,'docker')
    backup=new_backup()
    backup_configs(backup)
    from logging_stack import install_files, validate_alloy, docker, profile_args
    validate_alloy(backend)
    install_files(backend)
    cmd(*docker(*profile_args(backend),'config','-q'))
    # Create stopped target containers and local named volumes before copying.
    cmd(*docker(*profile_args(backend),'create','--no-build','loki','alloy'),timeout=600)
    volume=docker_volume_path()
    if any(volume.iterdir()):raise RuntimeErrorSafe('Volume Docker préexistant non vide : écrasement interdit')
    for unit in UNITS:cmd('systemctl','stop',unit)
    try:
        if DATA.is_dir():
            from tool_banner import Activity
            with Activity('Sauvegarde et copie des données Loki'):
                shutil.copytree(DATA,backup/'loki-data',symlinks=False)
                shutil.copytree(DATA,volume,dirs_exist_ok=True)
        for folder,dirs,files in os.walk(volume,followlinks=False):
            os.chown(folder,10001,10001)
            for name in dirs+files:
                path=Path(folder)/name
                if not path.is_symlink():os.chown(path,10001,10001)
        cmd(*docker(*profile_args(backend),'up','-d','--no-build','loki','alloy'),timeout=600)
        wait_ready();verify_canary()
    except Exception:
        cmd(*docker(*profile_args(backend),'stop','loki','alloy'),check=False)
        for unit in UNITS:cmd('systemctl','start',unit,check=False)
        raise
    save_state('docker')
    # Purge only if AUTOKOM's managed native overrides prove it owned install.
    managed=((NATIVE/'.autokom-installed').exists() and
             Path('/etc/systemd/system/loki.service.d/90-autokom.conf').exists() and
             Path('/etc/systemd/system/alloy.service.d/90-autokom.conf').exists())
    for unit in UNITS:cmd('systemctl','disable','--now',unit,check=False)
    if managed:
        cmd('apt-get','purge','-y','loki','alloy',timeout=600)
        for unit in UNITS:
            Path('/etc/systemd/system/'+unit+'.service.d/90-autokom.conf').unlink(missing_ok=True)
        cmd('systemctl','daemon-reload')
        if NATIVE.is_dir():shutil.rmtree(NATIVE)
        if DATA.is_dir():shutil.rmtree(DATA)
    else:
        print('  ! Paquets natifs non identifiés comme gérés : arrêtés mais purge manuelle nécessaire')
    from logging_stack import provision_grafana
    from logging_monitoring import apply as apply_monitoring
    changed=provision_grafana();apply_monitoring()
    from logging_stack import sync_grafana_after_install
    sync_grafana_after_install(changed)
    print('  ✓ Couple Docker actif, ancien couple systemd désactivé ; sauvegarde : '+str(backup))


def apply(target,backend):
    if os.geteuid()!=0:raise RuntimeErrorSafe('sudo requis pour changer de mode')
    if target not in ('docker','systemd'):raise RuntimeErrorSafe('Mode inconnu')
    current=detected()
    if current=='mixed':raise RuntimeErrorSafe('Conflit : deux modes actifs')
    if target=='systemd':return migrate_to_native(backend)
    return migrate_to_docker(backend)
