#!/usr/bin/env python3
"""AUTOKOM host execution boundary, JSON over a permissioned Unix socket.

Only the dedicated autokom service account and root can request allowlisted
operations. This process runs outside Django. It never accepts shell commands,
Jenkins URL/path, container mount paths or an arbitrary executable.
"""
from __future__ import annotations

from autokom_nginx_bind_policy import https_listens, tls_detected

import fcntl
import http.client
import secrets
import ipaddress
import json
import logging
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import base64
import urllib.request
import threading
import autokom_internal_ingress as ingress

logger = logging.getLogger('autokom.runtime.broker')
SOCKET = Path('/run/autokom/platform-control.sock')
STATE = Path('/var/lib/autokom/client-runtime')
IMAGES = Path('/var/lib/autokom/build-artifacts')
PROXY = Path('/etc/nginx/sites-available')
ENABLED = Path('/etc/nginx/sites-enabled')
WEBROOT = Path('/var/www/autokom-acme')
CLOUDFLARE_CONFIG = Path('/etc/autokom/secrets/cloudflare-dns.env')
RUNTIME_CONFIG = Path('/etc/autokom/secrets/runtime.env')
UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
HOSTNAME = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
MAX_REQUEST = 512 * 1024

class BrokerError(Exception):
    pass

def valid_id(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise BrokerError('Identifiant AUTOKOM invalide.')
    return value

def valid_hostname(value):
    if not isinstance(value, str) or not HOSTNAME.fullmatch(value) or value.endswith(('.local','.invalid','.test','.example')):
        raise BrokerError('Nom DNS public invalide.')
    return value

def _system_component(argv):
    """Safe, bounded classification; never include user-controlled arguments."""
    name = Path(argv[0]).name if argv else ''
    return name if name in ('nginx', 'certbot', 'dig', 'systemctl', 'docker') else 'runtime'


def run(argv, *, timeout=30, input=None, max_output=16000, pass_fds=()):
    if not isinstance(argv, list) or any(not isinstance(v,str) for v in argv):
        raise BrokerError('Arguments système non autorisés.')
    try:
        p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout, check=False, pass_fds=pass_fds,
                           env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning('AUTOKOM_SYSTEM_FAILURE component=%s reason=execution_unavailable',
                       _system_component(argv))
        raise BrokerError('AUTOKOM_SYSTEM_UNAVAILABLE: Action système indisponible ou expirée.') from exc
    if p.returncode:
        # Never log argv, stdout, stderr: commands can contain client secrets.
        component = _system_component(argv)
        logger.warning('AUTOKOM_SYSTEM_FAILURE component=%s exit_status=%d',
                       component, p.returncode)
        raise BrokerError('AUTOKOM_'+component.upper()+'_COMMAND_FAILED: Échec de commande contrôlée.')
    return p.stdout[:max_output]

def jenkins_config():
    # runtime.env is the protected source of truth, including after a token rotation.
    # Do not continue using credentials cached in a long-running systemd process.
    configuration = _runtime_dns_env()
    url = configuration.get('AUTOKOM_JENKINS_URL','').rstrip('/')
    parts = urlsplit(url)
    if parts.scheme != 'https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost')):
        raise BrokerError('Jenkins doit utiliser HTTPS ou loopback.')
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise BrokerError('Jenkins non configuré.')
    job = configuration.get('AUTOKOM_JENKINS_JOB','autokom-client-deploy')
    if not re.fullmatch(r'[\w.-]{1,100}',job):
        raise BrokerError('Job Jenkins invalide.')
    user, token = configuration.get('AUTOKOM_JENKINS_USER',''), configuration.get('AUTOKOM_JENKINS_API_TOKEN','')
    if not user or not token:
        raise BrokerError('Identifiants du compte Jenkins de service absents.')
    return url, '/job/' + quote(job,safe=''), user, token

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def jenkins_http(method, path, data=None):
    url, job, user, token = jenkins_config()
    if not path.startswith('/') or '//' in path or '..' in path:
        raise BrokerError('Chemin Jenkins refusé.')
    auth = base64.b64encode(f'{user}:{token}'.encode()).decode()
    body = None if data is None else urlencode(data).encode()
    req = Request(url+path, data=body, method=method, headers={'Authorization':'Basic '+auth,
                  'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'})
    try:
        with build_opener(NoRedirect()).open(req,timeout=18) as response:
            result = response.read(65536)
            return response.status,dict(response.headers),result
    except HTTPError as exc:
        # Only 404 is used to detect a missing job; do not expose full HTTP body.
        return exc.code,dict(exc.headers),b''
    except (URLError, TimeoutError, OSError) as exc:
        raise BrokerError('Jenkins injoignable.') from exc

def _isolated_agent_available():
    """Fail closed before queueing work if the dedicated non-controller agent is offline.

    Read-only Jenkins API. No customer-provided node names and no token in logs.
    """
    path = '/computer/api/json?tree=computer%5BdisplayName,offline,temporarilyOffline,numExecutors,assignedLabels%5Bname%5D%5D'
    code, _, body = jenkins_http('GET', path)
    if code != 200:
        raise BrokerError('Impossible de vérifier les agents de construction Jenkins.')
    try:
        computers = json.loads(body).get('computer', [])
    except (ValueError, TypeError):
        raise BrokerError('État des agents Jenkins illisible.')
    if not isinstance(computers, list):
        raise BrokerError('Liste des agents Jenkins invalide.')
    for computer in computers:
        if not isinstance(computer, dict):
            continue
        name = computer.get('displayName', '')
        # NEVER schedule untrusted customer code on Jenkins' built-in controller.
        if name in ('Built-In Node', 'Jenkins', 'master', ''):
            continue
        labels = {label.get('name') for label in (computer.get('assignedLabels') or [])
                  if isinstance(label, dict)}
        if ('autokom-isolated' in labels and computer.get('offline') is False
                and computer.get('temporarilyOffline') is not True
                and type(computer.get('numExecutors')) is int
                and computer['numExecutors'] >= 1):
            return True
    raise BrokerError('Agent de construction isolé indisponible. Aucune exécution envoyée en file.')


def jenkins_action(action, params):
    _,job,_,_ = jenkins_config()
    if action == 'jenkins.dispatch':
        rid = valid_id(params.get('run_id'))
        ingress.preflight(run, PROXY, ENABLED)  # fail before an unnecessary Jenkins build
        _isolated_agent_available()
        status,headers,_ = jenkins_http('POST',job+'/buildWithParameters',{'AUTOKOM_RUN_ID':rid})
        if status not in (200,201,202):raise BrokerError('Jenkins a refusé le déploiement.')
        location = headers.get('Location','')
        m = re.search(r'/queue/item/(\d+)/?$',location)
        if not m:raise BrokerError('Jenkins ne retourne pas de numéro de file.')
        return {'external_id':'Q:'+m.group(1)}
    external = params.get('external_id','')
    if not isinstance(external,str) or not re.fullmatch(r'[QB]:[1-9]\d{0,12}',external):
        raise BrokerError('Numéro de build Jenkins invalide.')
    kind,num = external.split(':',1)
    if action == 'jenkins.state':
        path = f'/queue/item/{num}/api/json' if kind=='Q' else f'{job}/{num}/api/json'
        status,_,body=jenkins_http('GET',path)
        if status!=200:raise BrokerError('État Jenkins indisponible.')
        data=json.loads(body)
        if kind=='Q':
            if data.get('cancelled'):return {'status':'CANCELLED','external_id':external}
            executable=data.get('executable') or {}
            n=executable.get('number')
            return {'status':'RUNNING','external_id':('B:'+str(n)) if type(n)==int and n>0 else external}
        if data.get('building'):return {'status':'RUNNING','external_id':external}
        mapping={'SUCCESS':'SUCCEEDED','FAILURE':'FAILED','UNSTABLE':'FAILED','ABORTED':'CANCELLED','NOT_BUILT':'BLOCKED'}
        if data.get('result') not in mapping:raise BrokerError('État Jenkins non déterminé.')
        return {'status':mapping[data['result']],'external_id':external}
    if action == 'jenkins.console':
        if kind!='B':return {'text':'','offset':int(params.get('offset',0))}
        start=params.get('offset',0)
        if type(start)!=int or start<0 or start>100_000_000:raise BrokerError('Position de log invalide.')
        status,headers,body=jenkins_http('GET',f'{job}/{num}/logText/progressiveText?start={start}')
        if status!=200:raise BrokerError('Logs Jenkins indisponibles.')
        chunk=body[:32000]
        offset=start+len(chunk) if len(body)>len(chunk) else max(start,int(headers.get('X-Text-Size',start)))
        return {'text':chunk.decode('utf-8','replace'),'offset':offset}
    if action == 'jenkins.cancel':
        status,_,_=jenkins_http('POST',f'/queue/cancelItem?id={num}' if kind=='Q' else f'{job}/{num}/stop',{})
        if status not in (200,201,202,302):raise BrokerError('Annulation Jenkins refusée.')
        return {'cancel_requested':True}
    raise BrokerError('Action Jenkins non reconnue.')

def _safe_env(values):
    if not isinstance(values,dict) or len(values)>100:raise BrokerError('Variables trop nombreuses.')
    for key,value in values.items():
        if not isinstance(key,str) or not KEY.fullmatch(key) or key.startswith(('AUTOKOM_', 'DOCKER_')):
            raise BrokerError('Nom de variable réservé ou invalide.')
        if not isinstance(value,str) or len(value)>32768 or '\x00' in value or '\r' in value or '\n' in value:
            raise BrokerError('Valeur de variable refusée (retour ligne non pris en charge).')
    return values

def _docker_loopback_port(container, container_port):
    """Read the port allocated atomically by Docker; never use a scan-then-bind race."""
    name = valid_container_name(container)
    if type(container_port) is not int or not 1 <= container_port <= 65535:
        raise BrokerError('Port interne du service invalide.')
    raw = run(['docker', 'container', 'inspect', name,
               '--format', '{{json .NetworkSettings.Ports}}'], timeout=12, max_output=8192)
    try:
        bindings = json.loads(raw)[f'{container_port}/tcp']
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise ValueError('Binding multiple')
        mapping = bindings[0]
        if mapping['HostIp'] != '127.0.0.1':
            raise ValueError('Non-loopback binding')
        selected = int(mapping['HostPort'])
        if not (1024 <= selected <= 65535):
            raise ValueError('Invalid allocated port')
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        raise BrokerError('Publication locale Docker non conforme ; aucun accès public autorisé.')
    return selected


def valid_container_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'autokom-[a-f0-9]{20}', name):
        raise BrokerError('Conteneur AUTOKOM invalide.')
    return name


def _env_file(env,run_id,values):
    base=STATE/env
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(base,0o700)
    name=base/(run_id+'.env')
    with tempfile.NamedTemporaryFile(mode='w',dir=base,delete=False,prefix='.env-',encoding='utf-8') as fd:
        os.chmod(fd.name,0o600)
        for k,v in _safe_env(values).items():
            fd.write(k+'='+v+'\n')
        tmp=fd.name
    os.replace(tmp,name)
    return name

def _load_state(environment):
    path=STATE/environment/'current.json'
    if not path.exists():return None
    doc=json.loads(path.read_text(encoding='utf-8'))
    if doc.get('environment_id')!=environment:raise BrokerError('État du runtime incohérent.')
    return doc

def _save_state(environment,doc):
    path=STATE/environment/'current.json'
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,prefix='.state-',encoding='utf-8') as fd:
        os.chmod(fd.name,0o600)
        json.dump(doc,fd)
        fd.write('\n')
        tmp=fd.name
    os.replace(tmp,path)

def _docker_deploy(params):
    """Serialize all Docker reservations to prevent local TCP port collisions."""
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(STATE/'.deploy.lock',os.O_WRONLY|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        return _docker_deploy_inner(params)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def _docker_deploy_inner(params):
    environment=valid_id(params.get('environment_id'))
    rid=valid_id(params.get('run_id'))
    cpu=params.get('cpu_millicores')
    mem=params.get('memory_mb')
    storage=params.get('storage_mb')
    port=params.get('internal_port')
    replicas=params.get('replicas')
    health=params.get('healthcheck','/')
    if any(type(n)!=int for n in (cpu,mem,storage,port,replicas)) or not (100<=cpu<=8000 and 128<=mem<=32768 and 128<=storage<=262144 and 1<=port<=65535 and replicas==1):
        raise BrokerError('Ressources invalides ou haute disponibilité non disponible.')
    if not isinstance(health,str) or not health.startswith('/') or len(health)>128 or any(x in health for x in ('?','#','%','\\','\r','\n')):
        raise BrokerError('Healthcheck invalide.')
    ingress.preflight(run, PROXY, ENABLED)
    current=_load_state(environment)
    if current and current.get('run_id')==rid:
        if current.get('ingress_mode') == ingress.MODE and not ingress.probe_relay(current['name'],current['port']):
            raise BrokerError('AUTOKOM_INGRESS_NOT_READY: Relais local interrompu.')
        return {'port':current['port'],'container':current['name'],'idempotent':True}
    name='autokom-'+rid.replace('-','')[:20]
    already=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=12)
    if already.returncode==0:
        raise BrokerError('Conteneur de ce run déjà présent hors transaction : réconciliation manuelle requise.')
    artifact=IMAGES/(rid+'.tar')
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size>8*1024**3 or artifact.stat().st_size<1024:
        raise BrokerError('Archive locale Jenkins introuvable ou non autorisée.')
    # Also refuse builds without enough free space for image import.
    if shutil.disk_usage(IMAGES).free < max(256*1024**2, artifact.stat().st_size*2):
        raise BrokerError('AUTOKOM_DISK_LOW: Stockage insuffisant pour importer l’image.')
    # Fixed trusted tag: only BuildKit-produced images in AUTOKOM's bounded directory.
    tag='autokom-local:'+rid
    # Open no-follow and keep the same inode throughout Docker import (prevent TOCTOU).
    fd = os.open(artifact, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>8*1024**3 or st.st_size<1024:
            raise BrokerError('Archive Docker invalide.')
        run(['docker','load','--input',f'/proc/self/fd/{fd}'],timeout=240,pass_fds=(fd,))
    finally:
        os.close(fd)
    run(['docker','image','inspect',tag,'--format','{{.Id}}'])
    network='autokom-'+environment.replace('-','')[:20]
    inspect=subprocess.run(['docker','network','inspect',network],capture_output=True,text=True,timeout=10)
    if inspect.returncode:
        run(['docker','network','create','--internal','--driver','bridge',network],timeout=30)
    else:
        try:
            networks=json.loads(inspect.stdout)
            if len(networks)!=1 or networks[0].get('Internal') is not True or networks[0].get('Driver')!='bridge':
                raise BrokerError('Réseau existant non privé : aucune connexion Docker.')
        except (ValueError,TypeError,KeyError) as exc:
            raise BrokerError('Réseau Docker existant non vérifiable.') from exc
    variables=_safe_env(params.get('variables') or {})
    envfile=_env_file(environment,rid,variables)
    # Customer processes run as UID 10001, without extra capabilities, mounts or Docker socket.
    argv=['docker','run','-d','--name',name,'--network',network,
          '--cpus',str(cpu/1000),'--memory',f'{mem}m','--memory-swap',f'{mem}m',
          '--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges',
          '--read-only','--tmpfs',f'/tmp:rw,noexec,nosuid,size={min(storage,256)}m',
          '--log-driver','json-file','--log-opt','max-size=10m','--log-opt','max-file=3',
          '--user','10001:10001',
           '--env-file',str(envfile),'--restart','unless-stopped',tag]
    relay = None
    try:
        try:
            run(argv,timeout=60)
        finally:
            envfile.unlink(missing_ok=True)  # secrets are never kept as host .env files after create
        # Nginx owns the trusted loopback listener. Customer has NO published ports.
        relay = ingress.create(name, network, port, execute=run, available=PROXY, enabled=ENABLED)
        external_port = relay['port']
        from urllib.request import build_opener
        health_opener = build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline=time.monotonic()+75
        while time.monotonic()<deadline:
            try:
                req=Request(f'http://127.0.0.1:{external_port}{health}',headers={'Host':'localhost'})
                with health_opener.open(req,timeout=2) as resp:
                    if 200<=resp.status<400:break
            except Exception:pass
            time.sleep(2)
        else:raise BrokerError('L’application ne répond pas à son healthcheck. Ancienne version conservée.')
        previous=_load_state(environment)
        doc={'environment_id':environment,'run_id':rid,'port':external_port,'name':name,'network':network,'healthcheck':health,**relay}
        # Persist previous BEFORE current so a failed snapshot cannot corrupt the active state.
        prior_path=STATE/environment/'previous.json'
        if previous:
            if prior_path.exists():
                raise BrokerError('AUTOKOM_PREVIOUS_PENDING: Ancienne publication non finalisée.')
            with tempfile.NamedTemporaryFile('w',dir=prior_path.parent,delete=False,prefix='.previous-',encoding='utf-8') as fd:
                os.chmod(fd.name,0o600);json.dump(previous,fd);fd.write('\n');tmp=fd.name
            os.replace(tmp,prior_path)
        try:
            _save_state(environment,doc)
        except Exception:
            if previous:prior_path.unlink(missing_ok=True)
            raise
        return {'port':external_port,'container':name,'previous':previous.get('name') if previous else None}
    except Exception:
        if relay is not None:
            try:
                ingress.remove(name, execute=run, available=PROXY, enabled=ENABLED)
            except Exception:
                logger.error('AUTOKOM_INGRESS_CLEANUP_FAILED')
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=20)
        raise
def _rollback(params):
    env=valid_id(params.get('environment_id'))
    rid=valid_id(params.get('run_id'))
    state=_load_state(env)
    if not state or state['run_id']!=rid:
        return {'rolled_back':False}
    old_path=STATE/env/'previous.json'
    previous=json.loads(old_path.read_text()) if old_path.is_file() else None
    if previous:
        _save_state(env,previous)
    else:
        (STATE/env/'current.json').unlink(missing_ok=True)
    run(['docker','rm','-f',state['name']],timeout=20)
    # If some vhosts were already switched to the failed release, repoint them
    # to the last healthy runtime (or remove their newly staged vhosts).
    for owner in STATE.glob('domain-owner-*.txt'):
        if owner.read_text(encoding='utf-8').strip()!=env:continue
        host=owner.name[len('domain-owner-'):-len('.txt')]
        if not HOSTNAME.fullmatch(host):continue
        conf=PROXY/('autokom-customer-'+host+'.conf')
        link=ENABLED/conf.name
        if previous:
            conf.write_text(_nginx_config(host,previous['port'],tls=True),encoding='utf-8')
        else:
            conf.unlink(missing_ok=True);link.unlink(missing_ok=True);owner.unlink(missing_ok=True)
    try:
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
    except BrokerError:
        raise BrokerError('État Docker restauré ; réconciliation manuelle des vhosts Nginx nécessaire.')
    if state.get('ingress_mode') == ingress.MODE:
        ingress.remove(state['name'], execute=run, available=PROXY, enabled=ENABLED)
    old_path.unlink(missing_ok=True)
    return {'rolled_back':True,'previous_available':previous is not None}


def _commit(params):
    env=valid_id(params.get('environment_id'))
    rid=valid_id(params.get('run_id'))
    state=_load_state(env)
    if not state or state.get('run_id')!=rid:
        raise BrokerError('Version active différente du déploiement demandé.')
    previous_path=STATE/env/'previous.json'
    if previous_path.is_file():
        previous=json.loads(previous_path.read_text())
        if previous.get('name') and previous['name'] != state['name']:
            old_name=valid_container_name(previous['name'])
            present=subprocess.run(['docker','container','inspect',old_name],capture_output=True,timeout=12)
            if present.returncode==0:
                run(['docker','rm','-f',old_name],timeout=20)
            if previous.get('ingress_mode') == ingress.MODE:
                ingress.remove(old_name, execute=run, available=PROXY, enabled=ENABLED)
        previous_path.unlink(missing_ok=True)
    return {'committed':True}


def _dig(record,hostname):
    """Only publicly observed DNS counts, not a local split-horizon BIND zone."""
    if record not in ('A','TXT'):
        raise BrokerError('Type DNS refusé.')
    answers=[]
    for resolver in ('1.1.1.1','8.8.8.8'):
        output=run(['dig','@'+resolver,'+short','+time=2','+tries=1',record,hostname],
                   timeout=6,max_output=3000)
        answers.append({line.strip().strip('"').rstrip('.') for line in output.splitlines() if line.strip()})
    return sorted(answers[0] & answers[1])


def _check_dns(host,token,managed):
    valid_hostname(host)
    ingress=_runtime_dns_env().get('AUTOKOM_INGRESS_IPV4','')
    try:ipaddress.IPv4Address(ingress)
    except ValueError:raise BrokerError('AUTOKOM_INGRESS_IPV4_MISSING: Adresse IPv4 publique non configurée.')
    if managed:
        suffix=_runtime_dns_env().get('AUTOKOM_MANAGED_DOMAIN_SUFFIX','').strip('.').lower()
        if not suffix or not host.endswith('.'+suffix) or host==suffix:
            raise BrokerError('AUTOKOM_DOMAIN_SUFFIX_MISMATCH: Sous-domaine hors de la zone gérée.')
    else:
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):
            raise BrokerError('Preuve de domaine manquante.')
        if 'autokom-verification='+token not in _dig('TXT','_autokom-challenge.'+host):
            raise BrokerError('Enregistrement TXT de propriété manquant.')
    if ingress not in _dig('A',host):
        raise BrokerError('A record DNS ne pointe pas vers l’ingress AUTOKOM.')
    return {'verified':True,'ipv4':ingress}

def _runtime_dns_env():
    """Read authoritative root-owned runtime DNS settings for *every* request.

    A CLI env edit must not leave DNS/HTTPS using the stale systemd process
    environment. Reject unexpected permissions and symlinks; never print secrets.
    The process environment is only a backward-compatible fallback when no
    centralized runtime file exists (including the isolated test environment).
    """
    path = RUNTIME_CONFIG
    if path.is_symlink():
        raise BrokerError('AUTOKOM_RUNTIME_ENV_PERMISSIONS: Lien symbolique interdit.')
    if not path.exists():
        return os.environ
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or (stat.S_IMODE(details.st_mode) & 0o077):
            raise BrokerError('AUTOKOM_RUNTIME_ENV_PERMISSIONS: Configuration runtime attendue root:0600.')
        from autokom_env_manager import read_env
        return read_env(path)
    except BrokerError:
        raise
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        raise BrokerError('AUTOKOM_RUNTIME_ENV_INVALID: Configuration runtime illisible.') from exc


def _domain_config_check(params):
    """Compare actual reserved names and Django settings to the broker policy.

    This is strictly read-only and must run BEFORE Jenkins dispatch. Do not
    weaken the allowed suffix or silently authorize a different Cloudflare zone.
    """
    config = _cloudflare_settings()
    suffix = config['suffix']
    declared = params.get('managed_suffix')
    if declared is not None:
        if not isinstance(declared, str) or declared != declared.strip('.').lower() or not HOSTNAME.fullmatch('probe.' + declared):
            raise BrokerError('AUTOKOM_DOMAIN_PREFLIGHT_INVALID: Suffixe applicatif invalide.')
        if declared != suffix:
            raise BrokerError('AUTOKOM_DOMAIN_SUFFIX_MISMATCH: Suffixes Django et broker différents.')
    hosts = params.get('managed_hosts')
    if hosts is not None:
        if (not isinstance(hosts, list) or not 1 <= len(hosts) <= 20
                or len(set(map(str, hosts))) != len(hosts)):
            raise BrokerError('AUTOKOM_DOMAIN_PREFLIGHT_INVALID: Liste des domaines gérés invalide.')
        for host in hosts:
            valid_hostname(host)
            if (host != host.strip('.').lower() or not host.endswith('.' + suffix)
                    or (config['zone_name'] and not host.endswith('.' + config['zone_name']))):
                raise BrokerError('AUTOKOM_DOMAIN_SUFFIX_MISMATCH: Domaine réservé hors de la zone autorisée.')
        email = _runtime_dns_env().get('AUTOKOM_ACME_EMAIL', '')
        if not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise BrokerError('AUTOKOM_ACME_EMAIL_MISSING: Adresse de contact TLS absente.')
    return {'ready': True, 'ingress_ipv4': config['address'],
            'managed_suffix': suffix, 'dns_zone': config['zone_name']}


def _cloudflare_settings():
    """Resolve the root-only DNS configuration for *each* request.

    This also picks up a completed `autokom dns cloudflare env setup` without
    waiting for an unrelated platform rebuild or systemd environment reload.
    Never log, return, or expose the API token through the broker socket.
    """
    conf = {}
    path = CLOUDFLARE_CONFIG
    if path.is_symlink():
        raise BrokerError('AUTOKOM_CF_SECRETS_PERMISSIONS: Lien symbolique interdit.')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or (stat.S_IMODE(info.st_mode) & 0o077):
            raise BrokerError('AUTOKOM_CF_SECRETS_PERMISSIONS: Configuration DNS attendue root:0600.')
        from autokom_env_manager import read_env
        try:
            conf = read_env(path)
        except Exception as exc:
            raise BrokerError('AUTOKOM_CF_SECRETS_INVALID: Configuration DNS illisible.') from exc
        # A configured file is authoritative: do not silently mix its token
        # with another Cloudflare account left in a process environment.
        zone = conf.get('CLOUDFLARE_ZONE_ID','').strip().lower()
        token = conf.get('CLOUDFLARE_API_TOKEN','').strip()
        zone_name = conf.get('CLOUDFLARE_ZONE_NAME','').strip('.').lower()
    else:
        zone = (os.environ.get('CLOUDFLARE_ZONE_ID') or os.environ.get('AUTOKOM_CLOUDFLARE_ZONE_ID') or '').strip().lower()
        token = (os.environ.get('CLOUDFLARE_API_TOKEN') or os.environ.get('AUTOKOM_CLOUDFLARE_TOKEN') or '').strip()
        zone_name = os.environ.get('CLOUDFLARE_ZONE_NAME','').strip('.').lower()
    runtime = _runtime_dns_env()
    suffix = runtime.get('AUTOKOM_MANAGED_DOMAIN_SUFFIX','').strip('.').lower()
    address = runtime.get('AUTOKOM_INGRESS_IPV4','').strip()
    if not suffix or not HOSTNAME.fullmatch('probe.'+suffix):
        raise BrokerError('AUTOKOM_DOMAIN_SUFFIX_MISSING: Suffixe des sites AUTOKOM absent.')
    if not re.fullmatch(r'[a-f0-9]{32}',zone) or not token:
        raise BrokerError('AUTOKOM_DOMAIN_CLOUDFLARE_CONFIG_MISSING: Configurer sudo autokom dns cloudflare env setup.')
    if zone_name and not (suffix==zone_name or suffix.endswith('.'+zone_name)):
        raise BrokerError('AUTOKOM_CF_ZONE_MISMATCH: Zone Cloudflare incompatible avec le domaine géré.')
    try:
        ip = ipaddress.IPv4Address(address)
        if not ip.is_global:
            raise ValueError('Adresse non publique')
    except ValueError as exc:
        raise BrokerError('AUTOKOM_INGRESS_IPV4_MISSING: IP publique ingress absente ou invalide.') from exc
    return {'zone':zone,'token':token,'zone_name':zone_name,'suffix':suffix,'address':address}


def _cloudflare_record(host):
    settings = _cloudflare_settings()
    suffix,zone,token = settings['suffix'],settings['zone'],settings['token']
    if not host.endswith('.'+suffix):
        raise BrokerError('AUTOKOM_DOMAIN_SUFFIX_MISMATCH: Domaine extérieur à la zone gérée.')
    ingress = settings['address']
    base='https://api.cloudflare.com/client/v4/zones/'+zone+'/dns_records'
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    zone_name=settings['zone_name']
    if zone_name and (host==zone_name or not host.endswith('.'+zone_name)):
        raise BrokerError('AUTOKOM_CF_ZONE_MISMATCH: Domaine hors de la zone Cloudflare configurée.')
    def cloudflare(request):
        try:
            with urllib.request.urlopen(request,timeout=12) as response:
                document=json.load(response)
        except HTTPError as exc:
            if exc.code in (401,403):
                raise BrokerError('AUTOKOM_CF_AUTH_DENIED: Vérifier les droits DNS Read et DNS Write du token Cloudflare.') from exc
            if exc.code==429:
                raise BrokerError('AUTOKOM_CF_RATE_LIMITED: Limite Cloudflare atteinte; nouvelle tentative programmée.') from exc
            raise BrokerError('AUTOKOM_CF_DNS_UNAVAILABLE: API Cloudflare indisponible.') from exc
        except (OSError,ValueError,TimeoutError) as exc:
            raise BrokerError('AUTOKOM_CF_DNS_UNAVAILABLE: API Cloudflare indisponible.') from exc
        if not isinstance(document,dict) or document.get('success') is not True:
            raise BrokerError('AUTOKOM_CF_DNS_REJECTED: Cloudflare refuse la configuration DNS.')
        return document
    # Check both A and CNAME. Cloudflare rejects CNAME/A coexistence; do not
    # overwrite records created outside AUTOKOM or hide an existing conflict.
    for record_type in ('CNAME','A'):
        req=Request(base+'?'+urlencode({'type':record_type,'name':host,'per_page':100}),headers=headers)
        data=cloudflare(req)
        results=[item for item in (data.get('result') or []) if isinstance(item,dict) and item.get('name','').rstrip('.').lower()==host]
        if results:
            if record_type=='CNAME' or len(results)!=1 or results[0].get('content')!=ingress or results[0].get('proxied') is not False:
                raise BrokerError('AUTOKOM_DOMAIN_DNS_CONFLICT: Enregistrement DNS existant différent.')
            return {'created':False}
    payload=json.dumps({'type':'A','name':host,'content':ingress,'ttl':120,'proxied':False}).encode()
    req=Request(base,data=payload,headers=headers,method='POST')
    cloudflare(req)
    return {'created':True}

def _nginx_config(host,port,tls=False):
    if type(port)!=int or not 1024<=port<=65535:raise BrokerError('Port upstream invalide.')
    safe_host=valid_hostname(host)
    if tls:
        cert='/etc/letsencrypt/live/'+safe_host
        return (f'server {{\n    listen 80;\n    server_name {safe_host};\n'
            f'    location ^~ /.well-known/acme-challenge/ {{ root {WEBROOT}; default_type text/plain; try_files $uri =404; }}\n'
            '    location / { return 308 https://$host$request_uri; }\n}\n'
            f'server {{\n' + https_listens() + f'    server_name {safe_host};\n'
            f'    ssl_certificate {cert}/fullchain.pem;\n'
            f'    ssl_certificate_key {cert}/privkey.pem;\n'
            '    ssl_protocols TLSv1.2 TLSv1.3;\n'
            '    client_max_body_size 16m;\n'
            f'    location / {{\n        proxy_pass http://127.0.0.1:{port};\n'
            '        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n'
            '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header X-Forwarded-Proto https;\n'
            '        proxy_connect_timeout 5s;\n        proxy_read_timeout 60s;\n    }\n}\n')
    return (f'server {{\n    listen 80;\n    server_name {safe_host};\n'
            f'    location ^~ /.well-known/acme-challenge/ {{ root {WEBROOT}; default_type text/plain; try_files $uri =404; }}\n'
            '    location / { return 503; }\n}\n')

def _prepare_public_acme_webroot():
    """Use the existing public AUTOKOM ACME root, outside private Django state.

    The broker itself has UMask=0077. Explicitly set traversal permissions
    only on these public root-owned ACME directories, NEVER on /var/lib/autokom.
    Fail closed instead of adopting attacker-owned or world-writable directories.
    """
    if not WEBROOT.is_absolute():
        raise BrokerError('AUTOKOM_ACME_WEBROOT_INVALID: Chemin ACME non absolu.')
    for parent in reversed(WEBROOT.parents):
        if parent.is_symlink() or not parent.is_dir():
            raise BrokerError('AUTOKOM_ACME_WEBROOT_INVALID: Parent symbolique ou non valide.')
    for item in (WEBROOT, WEBROOT / '.well-known',
                 WEBROOT / '.well-known' / 'acme-challenge'):
        if item.is_symlink():
            raise BrokerError('AUTOKOM_ACME_WEBROOT_INVALID: Lien symbolique interdit.')
        if not item.exists():
            item.mkdir(mode=0o755)
        st = item.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(st.st_mode) & 0o022):
            raise BrokerError('AUTOKOM_ACME_WEBROOT_INVALID: Répertoire non fiable ou insécurisé.')
        # These directories contain only public, short-lived ACME tokens.
        # Restrict all other AUTOKOM directories as before.
        os.chmod(item, 0o755)
    return WEBROOT


def _acme_ingress_local_ipv4():
    """Find the incoming private interface on NATed GCP/AWS instances."""
    try:
        response = subprocess.run(['ip', '-j', '-4', 'route', 'get', '1.1.1.1'],
                                  capture_output=True, text=True, timeout=3, check=False)
        if response.returncode:
            return None
        route = json.loads(response.stdout)[0]
        if not isinstance(route, dict) or str(route.get('dev', '')).startswith(
                ('docker', 'br-', 'veth', 'wg', 'tun')):
            return None
        addr = ipaddress.IPv4Address(route.get('prefsrc') or route.get('src'))
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            return None
        return str(addr)
    except (ValueError, KeyError, IndexError, TypeError, OSError, subprocess.TimeoutExpired):
        return None


def _preflight_public_acme_challenge(host):
    """Confirm Nginx can read a known public token on the inbound NIC.

    Does not log the token or expose secrets. No external certificate request.
    """
    valid_hostname(host)
    root = WEBROOT / '.well-known' / 'acme-challenge'
    name = 'autokom-preflight-' + secrets.token_hex(12)
    marker = 'autokom-http01-' + secrets.token_hex(16)
    file = root / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(file, flags, 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            os.fchmod(handle.fileno(), 0o644)  # broker inherits UMask=0077
            handle.write(marker)
        address = _acme_ingress_local_ipv4() or '127.0.0.1'
        connection = http.client.HTTPConnection(address, 80, timeout=6)
        try:
            connection.request('GET', '/.well-known/acme-challenge/' + name,
                               headers={'Host': host})
            response = connection.getresponse()
            body = response.read(256).decode('ascii', errors='replace')
            status = response.status
        except (OSError, http.client.HTTPException) as exc:
            raise BrokerError('AUTOKOM_ACME_HTTP01_UNREACHABLE: Nginx HTTP/80 inaccessible depuis l’interface locale.') from exc
        finally:
            connection.close()
        if status != 200 or body != marker:
            raise BrokerError(
                f'AUTOKOM_ACME_HTTP01_PREFLIGHT_FAILED: Nginx HTTP {status} sur {host} ; '
                'vérifier vhost et permissions de /var/www/autokom-acme. '
                'Aucune demande de certificat envoyée.')
    finally:
        file.unlink(missing_ok=True)


def _proxy_apply(host,environment):
    valid_hostname(host); valid_id(environment)
    state=_load_state(environment)
    if not state:raise BrokerError('Aucune version saine en service pour cet environnement.')
    # Do not steal an unrelated Nginx vhost.
    for directory in (PROXY, ENABLED):
        if directory.is_dir():
            for file in directory.iterdir():
                if file.is_file() and not file.is_symlink() and not file.name.startswith('autokom-customer-'):
                    try:content=file.read_text(encoding='utf-8',errors='replace')
                    except OSError:continue
                    if re.search(r'(?m)^\s*server_name\s+[^;]*\b'+re.escape(host)+r'\b',content):
                        raise BrokerError('Ce domaine est déjà défini dans un vhost Nginx existant.')
    name='autokom-customer-'+host+'.conf'
    owner=STATE/('domain-owner-'+host+'.txt')
    if owner.is_file() and owner.read_text(encoding='utf-8').strip()!=environment:
        raise BrokerError('Domaine déjà attribué à un autre environnement.')
    path=PROXY/name; symlink=ENABLED/name
    if path.exists() and (path.is_symlink() or not owner.is_file()):
        raise BrokerError('Vhost existant non géré par AUTOKOM : aucune modification.')
    _prepare_public_acme_webroot()
    old=path.read_text() if path.exists() else None
    def publish(content):
        with tempfile.NamedTemporaryFile('w',dir=PROXY,delete=False,prefix='.autokom-',encoding='utf-8') as f:
            f.write(content); tmp=f.name
        os.chmod(tmp,0o644);os.replace(tmp,path)
        if not symlink.exists():symlink.symlink_to(path)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
    phase = 'nginx_http'
    try:
        logger.info('AUTOKOM_DOMAIN_ACTIVATION phase=%s status=started', phase)
        publish(_nginx_config(host,state['port'],tls=False))
        # Validate the *real* Nginx vhost and token permissions before asking
        # Let's Encrypt; a successful nginx -t cannot detect HTTP 403.
        _preflight_public_acme_challenge(host)
        phase = 'acme_certificate'
        logger.info('AUTOKOM_DOMAIN_ACTIVATION phase=%s status=started', phase)
        # Certbot --keep-until-expiring reuses a currently valid certificate.
        if True:
            email=_runtime_dns_env().get('AUTOKOM_ACME_EMAIL','')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise BrokerError('AUTOKOM_ACME_EMAIL_MISSING: Adresse de contact TLS absente.')
            try:
                run(['certbot','certonly','--webroot','-w',str(WEBROOT),
                     '-d',host,'--agree-tos','--keep-until-expiring','--non-interactive','--email',email],timeout=130)
            except BrokerError as exc:
                raise BrokerError('AUTOKOM_ACME_FAILED: Certificat HTTPS non délivré ; vérifier DNS public et TCP/80.') from exc
        phase = 'nginx_https'
        logger.info('AUTOKOM_DOMAIN_ACTIVATION phase=%s status=started', phase)
        publish(_nginx_config(host,state['port'],tls=True))
        phase = 'ownership_commit'
        owner.write_text(environment,encoding='utf-8')
        os.chmod(owner,0o600)
        logger.info('AUTOKOM_DOMAIN_ACTIVATION phase=%s status=success', phase)
    except Exception as exc:
        # Keep the operator's event log useful but do not expose command output,
        # paths, environment values or third-party API responses.
        logger.warning('AUTOKOM_DOMAIN_ACTIVATION phase=%s status=failed category=%s',
                       phase, type(exc).__name__)
        if old is not None:path.write_text(old,encoding='utf-8')
        else:path.unlink(missing_ok=True);symlink.unlink(missing_ok=True)
        subprocess.run(['nginx','-t'],capture_output=True,timeout=10)
        subprocess.run(['systemctl','reload','nginx'],capture_output=True,timeout=15)
        raise
    return {'active':True,'hostname':host}


def _domain_inspect(params):
    """Read-only, ownership-verified status for a managed public vhost."""
    host=valid_hostname(params.get('hostname'))
    env=valid_id(params.get('environment_id'))
    conf=PROXY/('autokom-customer-'+host+'.conf')
    link=ENABLED/conf.name
    owner=STATE/('domain-owner-'+host+'.txt')
    if owner.is_symlink() or conf.is_symlink() or (link.exists() and not link.is_symlink()):
        raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISMATCH: Configuration non conforme.')
    if not owner.is_file() or not conf.is_file() or not link.is_symlink():
        return {'active':False}
    if owner.read_text(encoding='utf-8').strip()!=env:
        raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISMATCH: Vhost géré par un autre environnement.')
    if link.resolve()!=conf.resolve():
        raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISMATCH: Lien Nginx incohérent.')
    contents=conf.read_text(encoding='utf-8')
    state=_load_state(env)
    if not state or type(state.get('port')) is not int:
        return {'active':False}
    expected_upstream='proxy_pass http://127.0.0.1:'+str(state['port'])+';'
    active=('server_name '+host+';' in contents and
            tls_detected(contents) and expected_upstream in contents and
            '/etc/letsencrypt/live/'+host+'/fullchain.pem' in contents and
            (Path('/etc/letsencrypt/live')/host/'fullchain.pem').is_file())
    if active:
        # A valid local vhost is NOT proof of publicly reachable DNS.
        expected_ip=_runtime_dns_env().get('AUTOKOM_INGRESS_IPV4','')
        try:ipaddress.IPv4Address(expected_ip)
        except ValueError:
            raise BrokerError('AUTOKOM_INGRESS_IPV4_MISSING: IPv4 publique non configurée.')
        active=expected_ip in _dig('A',host)
    return {'active':bool(active)}


def _deactivate_owned_vhost(host, environment):
    """Remove only a host explicitly assigned to an environment by AUTOKOM.

    Never remove unrelated Nginx files; if Nginx validation/reload fails,
    restore the exact managed configuration before returning a failure.
    """
    valid_hostname(host); valid_id(environment)
    conf=PROXY/('autokom-customer-'+host+'.conf')
    link=ENABLED/conf.name
    owner=STATE/('domain-owner-'+host+'.txt')
    if not owner.is_file():
        if conf.exists() or conf.is_symlink() or link.exists() or link.is_symlink():
            raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISSING: Vhost présent sans propriété enregistrée.')
        return False
    if owner.is_symlink() or owner.read_text(encoding='utf-8').strip()!=environment:
        raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISMATCH: Domaine attribué ailleurs.')
    if (conf.is_symlink() or not conf.is_file() or not link.is_symlink()
            or link.resolve()!=conf):
        raise BrokerError('AUTOKOM_DOMAIN_VHOST_TAMPERED: Vhost géré non conforme.')
    original=conf.read_bytes()
    if ('server_name '+host+';').encode() not in original:
        raise BrokerError('AUTOKOM_DOMAIN_VHOST_TAMPERED: Configuration inattendue.')
    link.unlink()
    conf.unlink()
    try:
        run(['nginx','-t'],timeout=12)
        run(['systemctl','reload','nginx'],timeout=15)
    except Exception:
        # Retain the original ownership marker while reverting.
        with tempfile.NamedTemporaryFile('wb',dir=PROXY,delete=False,prefix='.autokom-restore-') as stream:
            stream.write(original)
            temporary=stream.name
        os.chmod(temporary,0o644)
        os.replace(temporary,conf)
        link.symlink_to(conf)
        try:
            run(['nginx','-t'],timeout=12)
            run(['systemctl','reload','nginx'],timeout=15)
        except Exception:
            logger.error('AUTOKOM_DOMAIN_RESTORE_INCOMPLETE')
        raise BrokerError('AUTOKOM_DOMAIN_DEACTIVATION_FAILED: Nginx refusé, configuration restaurée.')
    owner.unlink()
    return True


def _deprovision_environment(params):
    """Explicit, owner-checked and retryable runtime teardown, not filesystem deletion.

    Domain DNS records and domain ownership proofs stay in the database; no
    Cloudflare record or Let's Encrypt certificate is deleted blindly.
    """
    env=valid_id(params.get('environment_id'))
    hosts=params.get('hostnames')
    if not isinstance(hosts,list) or len(hosts)>100 or any(not isinstance(h,str) for h in hosts):
        raise BrokerError('AUTOKOM_DOMAIN_LIST_INVALID: Liste des domaines invalide.')
    if len(set(hosts))!=len(hosts):
        raise BrokerError('AUTOKOM_DOMAIN_LIST_INVALID: Doublons de domaine.')
    for host in hosts:valid_hostname(host)
    directory=STATE/env
    if directory.is_symlink():
        raise BrokerError('AUTOKOM_STATE_TAMPERED: Répertoire runtime non conforme.')
    states=[]
    for basename in ('current.json','previous.json'):
        record=directory/basename
        if record.is_symlink():
            raise BrokerError('AUTOKOM_STATE_TAMPERED: Fichier de runtime non conforme.')
        if record.is_file():
            try: data=json.loads(record.read_text(encoding='utf-8'))
            except (OSError,ValueError,UnicodeError) as exc:
                raise BrokerError('AUTOKOM_STATE_TAMPERED: État illisible.') from exc
            if not isinstance(data,dict) or data.get('environment_id')!=env:
                raise BrokerError('AUTOKOM_STATE_TAMPERED: État incohérent.')
            valid_container_name(data.get('name'))
            states.append((record,data))
    # Refuse unlisted managed domain ownership markers for the same environment.
    # A partial deletion must be retriable, not erase an unknown site.
    owned=[]
    for marker in STATE.glob('domain-owner-*.txt'):
        if marker.is_symlink():
            raise BrokerError('AUTOKOM_DOMAIN_OWNERSHIP_MISMATCH: Marqueur symbolique.')
        if marker.is_file() and marker.read_text(encoding='utf-8').strip()==env:
            hostname=marker.name[len('domain-owner-'):-len('.txt')]
            if hostname not in hosts:
                raise BrokerError('AUTOKOM_DOMAIN_LIST_INCOMPLETE: Domaines gérés manquants.')
            owned.append(hostname)
    # Stop external exposure before touching application containers.
    for host in owned:
        _deactivate_owned_vhost(host,env)
    for record,data in states:
        name=data['name']
        if data.get('ingress_mode')==ingress.MODE:
            ingress.remove(name,execute=run,available=PROXY,enabled=ENABLED)
        present=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=12)
        if present.returncode==0:
            run(['docker','rm','-f',name],timeout=25)
        record.unlink(missing_ok=True)
    return {'offline':True,'historical_state_retained':True}


def _activate_owned_domain(params):
    host=valid_hostname(params.get('hostname'))
    env=valid_id(params.get('environment_id'))
    _check_dns(host,params.get('token'),params.get('managed') is True)
    return _proxy_apply(host,env)


def handle(action,params):
    if not isinstance(params,dict):raise BrokerError('Paramètres invalides.')
    if action=='ping':
        try:jenkins_config();configured=True
        except BrokerError:configured=False
        return {'jenkins_configured':configured,'runtime':'docker-local','registry_required':False,'ha_ready':False}
    if action=='jenkins.identity':
        # Read-only, authenticated probe for host rotation health checks. Never
        # return user details, API tokens or Jenkins response bodies to clients.
        status,_,body=jenkins_http('GET','/whoAmI/api/json')
        if status!=200:raise BrokerError('Identité du compte de service Jenkins refusée.')
        try:
            authenticated=json.loads(body).get('authenticated') is True
        except (ValueError,AttributeError,TypeError):
            authenticated=False
        if not authenticated:raise BrokerError('Compte Jenkins non authentifié.')
        return {'authenticated':True}
    if action.startswith('jenkins.'):
        return jenkins_action(action,params)
    if action=='runtime.deploy':return _docker_deploy(params)
    if action=='runtime.rollback':return _runtime_lock_call(_rollback,params)
    if action=='runtime.commit':return _runtime_lock_call(_commit,params)
    if action=='runtime.deprovision':return _runtime_lock_call(_deprovision_environment,params)
    if action=='runtime.status':
        env=valid_id(params.get('environment_id'))
        return {'runtime':_load_state(env)}
    if action=='runtime.logs':
        env=valid_id(params.get('environment_id'))
        state=_load_state(env)
        if not state:return {'logs':[]}
        n=params.get('lines',80)
        if type(n)!=int or not 1<=n<=100:raise BrokerError('Nombre de lignes invalide.')
        try:
            process=subprocess.run(['docker','logs','--tail',str(n),'--timestamps',state['name']],
                                   timeout=12,capture_output=True,text=True,check=False)
        except (OSError,subprocess.TimeoutExpired):
            raise BrokerError('Logs Docker indisponibles.')
        if process.returncode:raise BrokerError('Lecture des logs refusée.')
        raw=(process.stdout+'\n'+process.stderr)[-24000:]
        lines=[line[:500] for line in raw.splitlines()[-n:]
               if not re.search(r'(?i)password|secret|token|authorization|api[_-]?key|gh[us]_|xsmtpsib-',line)]
        return {'logs':lines}
    if action=='domain.config.check':
        return _domain_config_check(params)  # validates actual reserved names before Jenkins
    if action=='domain.inspect':return _domain_inspect(params)
    if action=='domain.verify':
        return _check_dns(params.get('hostname'),params.get('token'),params.get('managed') is True)
    if action=='domain.dns.create':
        return _cloudflare_record(valid_hostname(params.get('hostname')))
    if action=='domain.activate':
        # Serialise Nginx / Certbot with runtime deploy, rollback and removal.
        # A domain retry cannot rewrite a vhost while another release changes state.
        return _runtime_lock_call(_activate_owned_domain,params)
    if action=='domain.deactivate':
        return _runtime_lock_call(lambda p: {'deactivated':_deactivate_owned_vhost(valid_hostname(p.get('hostname')),valid_id(p.get('environment_id')))},params)
    raise BrokerError('Opération non autorisée.')

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            creds=self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
            _,uid,_=struct.unpack('3i',creds)
            if uid not in (0,pwd.getpwnam('autokom').pw_uid):
                raise BrokerError('Identité du client Unix refusée.')
            line=self.rfile.readline(MAX_REQUEST+1)
            if len(line)>MAX_REQUEST or not line.endswith(b'\n'):
                raise BrokerError('Requête trop volumineuse.')
            request=json.loads(line)
            if not isinstance(request,dict):raise BrokerError('Format invalide.')
            result=handle(request.get('action',''),request.get('params',{}))
            response={'ok':True,'result':result}
        except Exception as exc:
            # Only the exception CATEGORY reaches the operator journal. No OAuth, env or args.
            safe = isinstance(exc,(BrokerError,ingress.IngressError))
            match = re.match(r'^(AUTOKOM_[A-Z0-9_]{3,80}):',str(exc)) if safe else None
            code = exc.code if isinstance(exc,ingress.IngressError) else (match.group(1) if match else ('AUTOKOM_BROKER_REFUSED' if safe else 'AUTOKOM_BROKER_INTERNAL_ERROR'))
            # Source function/line from this trusted module, NEVER exception text
            # or a traceback containing customer-controlled values.
            source = 'unknown'
            if not safe:
                tb = exc.__traceback__
                while tb is not None:
                    if tb.tb_frame.f_code.co_filename == __file__:
                        source = '%s:%d' % (tb.tb_frame.f_code.co_name, tb.tb_lineno)
                    tb = tb.tb_next
            logger.warning('Broker request denied: %s code=%s origin=%s',
                           type(exc).__name__,code,source)
            # No exception traceback, request content or secret values in response.
            response={'ok':False,'error':str(exc) if safe else 'Erreur interne du service AUTOKOM.', 'code':code}
        self.wfile.write((json.dumps(response,ensure_ascii=False)+'\n').encode())

class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=True

def _prepare_socket_directory():
    """Enforce the same root:autokom 0750 boundary after every restart.

    The systemd RuntimeDirectory= setting should already provide these
    attributes. Revalidating them here catches stale directories and prevents
    a misconfigured unit from silently breaking the Django worker.
    """
    directory = SOCKET.parent
    if directory.is_symlink():
        raise BrokerError('Répertoire de contrôle Unix non conforme.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    details = directory.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0:
        raise BrokerError('Propriétaire du répertoire Unix non conforme.')
    gid = pwd.getpwnam('autokom').pw_gid
    os.chown(directory, 0, gid)
    os.chmod(directory, 0o750)


def _runtime_lock_call(action, params):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd=os.open(STATE/'.deploy.lock',os.O_WRONLY|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        return action(params)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

def _ingress_scan(_unused):
    # A previously approved relay is updated ONLY when its Docker IP changed.
    # The same lock as deploy/rollback/commit prevents lost state updates.
    for folder in STATE.iterdir():
        if not folder.is_dir() or not UUID.fullmatch(folder.name):
            continue
        for filename in ('current.json','previous.json'):
            record=folder/filename
            if not record.is_file() or record.is_symlink():
                continue
            try:
                doc=json.loads(record.read_text(encoding='utf-8'))
                if not isinstance(doc,dict) or doc.get('environment_id')!=folder.name:
                    continue
                if ingress.reconcile(doc,execute=run,available=PROXY,enabled=ENABLED):
                    with tempfile.NamedTemporaryFile('w',dir=folder,delete=False,prefix='.ingress-state-',encoding='utf-8') as fd:
                        os.chmod(fd.name,0o600)
                        json.dump(doc,fd)
                        fd.write('\n')
                        tmp=fd.name
                    os.replace(tmp,record)
                    logger.info('AUTOKOM_INGRESS_IP_RECONCILED')
            except Exception:
                logger.warning('AUTOKOM_INGRESS_RECONCILE_PENDING')

def _ingress_watchdog():
    while True:
        try:
            _runtime_lock_call(_ingress_scan,None)
        except Exception:
            logger.warning('AUTOKOM_INGRESS_WATCHDOG_UNAVAILABLE')
        time.sleep(90)

def main():
    if os.geteuid()!=0:raise SystemExit('Lancer par systemd, utilisateur root.')
    _prepare_socket_directory()
    SOCKET.unlink(missing_ok=True)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    IMAGES.mkdir(parents=True,exist_ok=True,mode=0o750)
    threading.Thread(target=_ingress_watchdog,name='autokom-ingress-watchdog',daemon=True).start()
    with Server(str(SOCKET),Handler) as server:
        os.chown(SOCKET,0,pwd.getpwnam('autokom').pw_gid)
        os.chmod(SOCKET,0o660)
        server.serve_forever(poll_interval=0.4)

if __name__=='__main__':
    main()
