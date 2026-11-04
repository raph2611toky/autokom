#!/usr/bin/env python3
"""Centralised, interactive, fail-closed AUTOKOM service environment management.

All configuration values are stored exclusively under /etc/autokom/secrets;
never passed in argv, git or user-visible output. No .env template is used.
"""
from __future__ import annotations
import argparse
import getpass
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.request import Request, urlopen

SECRET_DIR = Path('/etc/autokom/secrets')
FILES = {'runtime': 'runtime.env', 'backend': 'backend.env', 'cloudflare': 'cloudflare-dns.env', 'frontend': 'frontend.env'}
OLD_FILES = {'runtime': Path('/etc/autokom/platform-runtime.env'), 'backend': Path('/etc/autokom/platform-backend.env')}
SAFE_KEYS = {
    'runtime': ('AUTOKOM_JENKINS_URL', 'AUTOKOM_JENKINS_JOB', 'AUTOKOM_JENKINS_USER',
                'AUTOKOM_JENKINS_API_TOKEN', 'AUTOKOM_INGRESS_IPV4', 'AUTOKOM_MANAGED_DOMAIN_SUFFIX',
                'AUTOKOM_ACME_EMAIL', 'AUTOKOM_RUNNER_SHARED_KEY'),
    'cloudflare': ('CLOUDFLARE_ZONE_ID', 'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ZONE_NAME'),
    'frontend': ('FRONTEND_URL', 'BACKEND_URL', 'VITE_AUTOKOM_API_BASE',
                 'VITE_AUTOKOM_FRONTEND_ONLY', 'VITE_AUTOKOM_API_ENABLED',
                 'VITE_AUTOKOM_GITHUB_ENABLED', 'VITE_AUTOKOM_AUTH_MODE'),
}
SENSITIVE = re.compile(r'(PASSWORD|SECRET|TOKEN|KEY|PASS|PRIVATE|CREDENTIAL|AUTH)', re.I)
_KEY = re.compile(r'^[A-Z][A-Z0-9_]{1,90}$')
_DOMAIN = re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$')

class EnvError(RuntimeError): pass


def file_path(kind):
    if kind not in FILES: raise EnvError('Type de configuration inconnu.')
    return SECRET_DIR / FILES[kind]


def require_root():
    if os.geteuid() != 0: raise EnvError('Utiliser sudo : configuration réservée à root.')


def tty():
    if not os.isatty(0): raise EnvError('Cette action nécessite un terminal interactif.')


def select(title, choices, default=1):
    tty()
    from menu import pick
    try:
        index=pick('AUTOKOM · '+title, choices, default=default, cancel_label='Annuler')
    except (KeyboardInterrupt, EOFError) as exc:
        raise EnvError('Opération annulée.') from exc
    if not index: raise EnvError('Opération annulée.')
    return choices[index-1]


def read_env(path):
    if not path.is_file(): return {}
    if path.is_symlink(): raise EnvError('Lien symbolique interdit pour la configuration.')
    data={}
    for line in path.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        if '=' not in line: raise EnvError('Ligne de configuration malformée (clé=valeur attendu).')
        k,v=line.split('=',1)
        if not _KEY.fullmatch(k): raise EnvError('Nom de variable invalide dans la configuration.')
        if (v.startswith('\"') and v.endswith('\"')) or (v.startswith("'") and v.endswith("'")):
            v=v[1:-1]
            if line.split('=',1)[1].startswith('\"'):
                v=v.replace('\\\"','\"').replace('\\\\','\\')
        # Compatibility with legacy .env files: last assignment wins.
        data[k]=v
    return data


def write_env(path, data):
    require_root()
    if path.is_symlink():raise EnvError('Écriture refusée sur un lien symbolique.')
    if path.parent != SECRET_DIR:raise EnvError('Tous les environnements doivent être dans /etc/autokom/secrets/.')
    if not all(_KEY.fullmatch(k) for k in data):raise EnvError('Clé de configuration invalide.')
    if any('\n' in v or '\r' in v or '\x00' in v or len(v)>8192 for v in data.values()):
        raise EnvError('Valeur refusée (multiligne ou caractère non autorisé).')
    SECRET_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(SECRET_DIR,0o700)
    fd, name=tempfile.mkstemp(prefix='.autokom-env-',dir=str(SECRET_DIR))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            # systemd EnvironmentFile accepts double-quoted values with escapes
            for key,value in sorted(data.items()):
                escaped=value.replace('\\','\\\\').replace('"','\\"')
                f.write(f'{key}="{escaped}"\n')
            f.flush();os.fsync(f.fileno())
        os.chown(name,0,0);os.chmod(name,0o600)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def backup(path):
    require_root()
    if not path.exists():return None
    if path.is_symlink():raise EnvError('Fichier symbolique refusé.')
    archive=SECRET_DIR/'archive'
    archive.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(archive,0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target=archive/(path.name+'.'+stamp+'.'+secrets.token_hex(4)+'.bak')
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'wb') as f:f.write(path.read_bytes())
    except BaseException:
        target.unlink(missing_ok=True);raise
    return target


def discover():
    """Only non-sensitive local facts; never silently assume external addresses."""
    found={}
    try:
        p=subprocess.run(['systemctl','is-active','jenkins'],capture_output=True,text=True,timeout=3)
        if p.stdout.strip()=='active':found['AUTOKOM_JENKINS_URL']='http://127.0.0.1:8080'
    except (OSError, subprocess.TimeoutExpired):pass
    found['AUTOKOM_JENKINS_JOB']='autokom-client-deploy'
    try:
        request=Request('http://169.254.169.254/latest/api/token',method='PUT',
                        headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
        opener=__import__('urllib.request',fromlist=['build_opener']).build_opener(
            __import__('urllib.request',fromlist=['ProxyHandler']).ProxyHandler({}))
        with opener.open(request,timeout=0.8) as r: token=r.read(512).decode()
        request=Request('http://169.254.169.254/latest/meta-data/public-ipv4',
                        headers={'X-aws-ec2-metadata-token':token})
        with opener.open(request,timeout=0.8) as r: ip=r.read(40).decode().strip()
        if ipaddress.ip_address(ip).is_global:found['AUTOKOM_INGRESS_IPV4']=ip
    except (OSError,ValueError,TimeoutError):pass
    try:
        from autokom_app_manager import STATE_FILE
        import json
        if STATE_FILE.is_file():
            state=json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if state.get('frontend'):found['FRONTEND_URL']=state['frontend']
            if state.get('domain'):found['BACKEND_URL']='https://'+state['domain']
    except (OSError,ValueError,ImportError,TypeError): pass
    try:
        from autokom_dns_cloudflare import CONFIG
        cf=read_env(CONFIG)
        if cf.get('CLOUDFLARE_ZONE_ID'):found['CLOUDFLARE_ZONE_ID']=cf['CLOUDFLARE_ZONE_ID']
        if cf.get('CLOUDFLARE_ZONE_NAME'):found['CLOUDFLARE_ZONE_NAME']=cf['CLOUDFLARE_ZONE_NAME']
    except (OSError,EnvError,ImportError):pass
    return found


def _is_sensitive(key):
    return key != 'VITE_AUTOKOM_AUTH_MODE' and bool(SENSITIVE.search(key))


def _default(kind,key,existing):
    if key in existing:return existing[key]
    found=discover()
    if key in found:return found[key]
    if key=='VITE_AUTOKOM_AUTH_MODE':return 'cookie'
    if key=='AUTOKOM_MANAGED_DOMAIN_SUFFIX': return 'apps.ikom.mg'
    if key=='AUTOKOM_ACME_EMAIL':return ''
    if key=='CLOUDFLARE_ZONE_NAME':return 'ikom.mg'
    return ''


def _prompt(key, default=''):
    if _is_sensitive(key):
        entered=getpass.getpass('  🔒 '+key+' (masqué, Entrée pour conserver) : ')
        return entered or default
    visible='[détecté]' if default else '[à renseigner]'
    print(f'  \033[32m│ {key} · {visible}\033[0m')
    value=input(f'  {key} [{default}] : ').strip()
    return value or default


def _validate(kind,values):
    for key,value in values.items():
        if not _KEY.fullmatch(key) or '\n' in value or '\r' in value:raise EnvError('Nom ou valeur invalide.')
    if kind=='runtime':
        if values.get('AUTOKOM_INGRESS_IPV4'):
            ip=ipaddress.ip_address(values['AUTOKOM_INGRESS_IPV4'])
            if ip.version!=4 or not ip.is_global:raise EnvError('IP ingress publique IPv4 requise.')
        if values.get('AUTOKOM_MANAGED_DOMAIN_SUFFIX') and not _DOMAIN.fullmatch(values['AUTOKOM_MANAGED_DOMAIN_SUFFIX']):
            raise EnvError('Suffixe DNS géré invalide.')
    if kind=='frontend':
        for name in ('FRONTEND_URL','BACKEND_URL'):
            if name in values and (not re.fullmatch(r'https://[a-z0-9.-]+',values[name]) or
                                   not _DOMAIN.fullmatch(values[name][8:])):
                raise EnvError('URL HTTPS invalide pour '+name)
        for name in ('VITE_AUTOKOM_FRONTEND_ONLY','VITE_AUTOKOM_API_ENABLED','VITE_AUTOKOM_GITHUB_ENABLED'):
            if name in values and values[name] not in ('true','false'):
                raise EnvError('Valeur booléenne invalide : '+name)
        if values.get('VITE_AUTOKOM_AUTH_MODE', 'cookie') not in ('cookie', 'bearer'):
            raise EnvError('Mode authentification frontend invalide (cookie ou bearer).')
        if values.get('VITE_AUTOKOM_API_BASE', '/api') != '/api':
            raise EnvError('La base API doit être /api (proxy Nginx).')
        if values.get('VITE_AUTOKOM_GITHUB_ENABLED','false') == 'true':
            raise EnvError('La liaison UI GitHub simulée ne peut pas être activée en production.')
    if kind=='cloudflare' and values.get('CLOUDFLARE_ZONE_ID') and not re.fullmatch(r'[a-fA-F0-9]{32}',values['CLOUDFLARE_ZONE_ID']):
        raise EnvError('Zone ID Cloudflare invalide (32 caractères hexadécimaux).')


def _systemd_override(name, path):
    # systemd reads file as manager (root) before dropping privileges.
    unit=Path('/etc/systemd/system')/f'{name}.service'
    if not unit.is_file():return False
    drop=Path('/etc/systemd/system')/(name+'.service.d')/'20-autokom-central-secrets.conf'
    drop.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    data='[Service]\nEnvironmentFile=\nEnvironmentFile='+str(path)+'\n'
    if name=='autokom-platform-broker':data+='EnvironmentFile=-'+str(file_path('cloudflare'))+'\n'
    previous=drop.read_text() if drop.exists() else None
    if previous!=data:
        fd,tmp=tempfile.mkstemp(dir=str(drop.parent),prefix='.unit-')
        with os.fdopen(fd,'w') as out:out.write(data)
        os.chmod(tmp,0o644);os.replace(tmp,drop)
    return True


def migrate(kind):
    """Adopt currently running env as source of truth; never overwrite without consent."""
    require_root()
    dest=file_path(kind);legacy=OLD_FILES.get(kind)
    if not legacy or not legacy.is_file():return False
    if legacy.is_symlink():raise EnvError('Ancienne configuration symbolique refusée.')
    new_data=read_env(legacy); current=read_env(dest)
    if current!=new_data:
        print(f'  ◈ Configuration {kind} héritée détectée : {legacy}')
        print(f'  › Destination centralisée : {dest} · secrets non affichés')
        choice=select('Migration des secrets '+kind,[
            'Adopter les paramètres du service actuel (recommandé)',
            'Conserver la configuration centralisée existante',
            'Annuler la migration'])
        if choice.startswith('Annuler'):raise EnvError('Migration annulée.')
        if choice.startswith('Adopter'):
            if dest.exists():backup(dest)
            write_env(dest,new_data)
    # Do NOT delete legacy while old units might still reference it.
    print(f'  ✓ Configuration centralisée : {dest}')
    return True


def _apply_units(kind):
    targets={'backend':['autokom-backend','autokom-deployment-worker'],
             'runtime':['autokom-platform-broker'], 'cloudflare':['autokom-platform-broker']}
    updated=False
    for name in targets.get(kind,[]):
        path=file_path('runtime') if kind=='cloudflare' else file_path(kind)
        updated|=_systemd_override(name,path)
    if updated:
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
        print('  ✓ systemd : daemon-reload (aucun service redémarré)')
        print('  › Les services liront le nouveau chemin au prochain redémarrage contrôlé.')


def show(kind):
    require_root()
    data=read_env(file_path(kind))
    print('  ◈ AUTOKOM · ENV · '+kind.upper())
    print('  › Fichier :',file_path(kind))
    if not data:print('  › Non configuré · utiliser « setup ».');return 0
    for k,v in sorted(data.items()):
        state='•••••• (configuré)' if _is_sensitive(k) and v else ('vide' if not v else v[:95])
        print(f'  \033[32m✓\033[0m {k:<36} {state}')
    return 0


def edit(kind,key=None):
    require_root();tty()
    path=file_path(kind);data=read_env(path)
    candidates=list(dict.fromkeys([*SAFE_KEYS.get(kind,()),*data.keys()]))
    if kind=='backend':candidates=list(data)
    if not candidates: raise EnvError('Aucune clé à modifier : exécuter setup.')
    if key is None:key=select('Modifier une variable '+kind,candidates)
    if key not in candidates:raise EnvError('Variable non autorisée.')
    value=_prompt(key,data.get(key,''))
    tentative={**data,key:value};_validate(kind,tentative)
    if tentative==data:print('  ✓ Aucun changement.');return 0
    backup(path)
    write_env(path,tentative)
    print('  ✓ Variable actualisée ; valeur non affichée.')
    return 0


def delete(kind,key=None):
    require_root();tty()
    path=file_path(kind);data=read_env(path)
    if not data: print('  › Aucune variable à supprimer.');return 0
    if key is None:key=select('Supprimer une variable '+kind,sorted(data))
    if key not in data:raise EnvError('Variable inexistante.')
    if input(f'  Saisir SUPPRIMER pour effacer {key} : ').strip()!='SUPPRIMER':
        raise EnvError('Suppression annulée.')
    backup(path); del data[key];write_env(path,data)
    print('  ✓ Variable supprimée. Le service actif n’a pas été redémarré.')
    return 0


def setup(kind):
    require_root();tty()
    migrate(kind)
    path=file_path(kind);data=read_env(path)
    print('  ◈ AUTOKOM · CONFIGURATION · '+kind.upper())
    print('  › Configuration trouvée :',path if data else 'aucune')
    print('  › Aucune valeur secrète n’est envoyée sur GitHub ni affichée.')
    if kind=='backend' and data:
        print('  ✓ Paramètres de service existants conservés. Utiliser « edit » pour modifier.')
    else:
        keys=SAFE_KEYS[kind]
        for key in keys:
            if key in data and data[key]:continue
            value=_prompt(key,_default(kind,key,data))
            if value:data[key]=value
        _validate(kind,data)
        if path.exists():backup(path)
        write_env(path,data)
        print('  ✓ Configuration sauvegardée :',path)
    _apply_units(kind)
    if kind=='runtime':
        print('  › Cloudflare : sudo autokom dns cloudflare env setup')
        print('  › Jenkins : sudo autokom runtime jenkins --check')
        print('  › Clé/token manquant : suivre https://www.jenkins.io/doc/book/using/remote-access-api/')
    if kind=='cloudflare':
        print('  › Jeton restreint DNS (Zone Read + Zone DNS Edit) : https://dash.cloudflare.com/profile/api-tokens')
    return 0


def main(kind,argv=None):
    p=argparse.ArgumentParser(prog=f'autokom {kind} env',description='Gestion sécurisée des variables AUTOKOM')
    sub=p.add_subparsers(dest='action')
    for name in ('show','setup','edit','delete'):
        item=sub.add_parser(name)
        if name in ('edit','delete'):item.add_argument('key',nargs='?')
    args=p.parse_args(argv)
    if not args.action:
        action=select('Secrets '+kind,['show','setup','edit','delete'])
    else:action=args.action
    if action=='show':return show(kind)
    if action=='setup':return setup(kind)
    if action=='edit':return edit(kind,getattr(args,'key',None))
    if action=='delete':return delete(kind,getattr(args,'key',None))
    return 1
