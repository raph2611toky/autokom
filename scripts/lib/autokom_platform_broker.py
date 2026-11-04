#!/usr/bin/env python3
"""AUTOKOM host execution boundary, JSON over a permissioned Unix socket.

Only the dedicated autokom service account and root can request allowlisted
operations. This process runs outside Django. It never accepts shell commands,
Jenkins URL/path, container mount paths or an arbitrary executable.
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
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

SOCKET = Path('/run/autokom/platform-control.sock')
STATE = Path('/var/lib/autokom/client-runtime')
IMAGES = Path('/var/lib/autokom/build-artifacts')
PROXY = Path('/etc/nginx/sites-available')
ENABLED = Path('/etc/nginx/sites-enabled')
WEBROOT = Path('/var/lib/autokom/acme')
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

def run(argv, *, timeout=30, input=None, max_output=16000, pass_fds=()):
    if not isinstance(argv, list) or any(not isinstance(v,str) for v in argv):
        raise BrokerError('Arguments système non autorisés.')
    try:
        p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout, check=False, pass_fds=pass_fds,
                           env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrokerError('Action système indisponible ou expirée.') from exc
    if p.returncode:
        # Never forward stdout/stderr verbatim: Docker and Jenkins can print secrets.
        raise BrokerError('L’opération système a échoué. Consulter journalctl du broker AUTOKOM.')
    return p.stdout[:max_output]

def jenkins_config():
    url = os.environ.get('AUTOKOM_JENKINS_URL','').rstrip('/')
    parts = urlsplit(url)
    if parts.scheme != 'https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost')):
        raise BrokerError('Jenkins doit utiliser HTTPS ou loopback.')
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise BrokerError('Jenkins non configuré.')
    job = os.environ.get('AUTOKOM_JENKINS_JOB','autokom-client-deploy')
    if not re.fullmatch(r'[\w.-]{1,100}',job):
        raise BrokerError('Job Jenkins invalide.')
    user, token = os.environ.get('AUTOKOM_JENKINS_USER',''), os.environ.get('AUTOKOM_JENKINS_API_TOKEN','')
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

def jenkins_action(action, params):
    _,job,_,_ = jenkins_config()
    if action == 'jenkins.dispatch':
        rid = valid_id(params.get('run_id'))
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

def _port_available():
    import socket as sock
    for candidate in range(21000,29000):
        with sock.socket() as s:
            try:s.bind(('127.0.0.1',candidate))
            except OSError:continue
            return candidate
    raise BrokerError('Aucun port local disponible.')

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
    current=_load_state(environment)
    if current and current.get('run_id')==rid:
        return {'port':current['port'],'container':current['name'],'idempotent':True}
    name='autokom-'+rid.replace('-','')[:20]
    already=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=12)
    if already.returncode==0:
        raise BrokerError('Conteneur de ce run déjà présent hors transaction : réconciliation manuelle requise.')
    artifact=IMAGES/(rid+'.tar')
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size>8*1024**3 or artifact.stat().st_size<1024:
        raise BrokerError('Archive locale Jenkins introuvable ou non autorisée.')
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
    external_port=_port_available()
    variables=_safe_env(params.get('variables') or {})
    envfile=_env_file(environment,rid,variables)
    # Customer processes run as UID 10001, without extra capabilities, mounts or Docker socket.
    argv=['docker','run','-d','--name',name,'--network',network,
          '--publish',f'127.0.0.1:{external_port}:{port}',
          '--cpus',str(cpu/1000),'--memory',f'{mem}m','--memory-swap',f'{mem}m',
          '--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges',
          '--read-only','--tmpfs',f'/tmp:rw,noexec,nosuid,size={min(storage,256)}m',
          '--log-driver','json-file','--log-opt','max-size=10m','--log-opt','max-file=3',
          '--user','10001:10001',
          '--env-file',str(envfile),'--restart','unless-stopped',tag]
    try:
        try:
            run(argv,timeout=60)
        finally:
            envfile.unlink(missing_ok=True)  # secrets are never kept as host .env files after create
        from urllib.request import urlopen
        deadline=time.monotonic()+75
        while time.monotonic()<deadline:
            try:
                req=Request(f'http://127.0.0.1:{external_port}{health}',headers={'Host':'localhost'})
                with urlopen(req,timeout=2) as resp:
                    if 200<=resp.status<400:break
            except Exception:pass
            time.sleep(2)
        else:raise BrokerError('L’application ne répond pas à son healthcheck. Ancienne version conservée.')
        previous=_load_state(environment)
        doc={'environment_id':environment,'run_id':rid,'port':external_port,'name':name,'network':network,'healthcheck':health}
        _save_state(environment,doc)
        # The previous container stays alive until all TLS proxies are installed.
        if previous:
            (STATE/environment/'previous.json').write_text(json.dumps(previous),encoding='utf-8')
            os.chmod(STATE/environment/'previous.json',0o600)
        return {'port':external_port,'container':name,'previous':previous.get('name') if previous else None}
    except Exception:
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
            try:run(['docker','rm','-f',previous['name']],timeout=20)
            except BrokerError:pass
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
    ingress=os.environ.get('AUTOKOM_INGRESS_IPV4','')
    try:ipaddress.IPv4Address(ingress)
    except ValueError:raise BrokerError('AUTOKOM_INGRESS_IPV4 requis (IP publique fixe).')
    if managed:
        suffix=os.environ.get('AUTOKOM_MANAGED_DOMAIN_SUFFIX','').strip('.').lower()
        if not suffix or not host.endswith('.'+suffix) or host==suffix:
            raise BrokerError('Sous-domaine non autorisé par IKOM.')
    else:
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):
            raise BrokerError('Preuve de domaine manquante.')
        if 'autokom-verification='+token not in _dig('TXT','_autokom-challenge.'+host):
            raise BrokerError('Enregistrement TXT de propriété manquant.')
    if ingress not in _dig('A',host):
        raise BrokerError('A record DNS ne pointe pas vers l’ingress AUTOKOM.')
    return {'verified':True,'ipv4':ingress}

def _cloudflare_record(host):
    suffix=os.environ.get('AUTOKOM_MANAGED_DOMAIN_SUFFIX','').strip('.').lower()
    zone=os.environ.get('AUTOKOM_CLOUDFLARE_ZONE_ID','')
    token=os.environ.get('AUTOKOM_CLOUDFLARE_TOKEN','')
    if not suffix or not host.endswith('.'+suffix) or not re.fullmatch(r'[a-f0-9]{32}',zone) or not token:
        raise BrokerError('Gestion Cloudflare IKOM non configurée.')
    ingress=os.environ.get('AUTOKOM_INGRESS_IPV4','')
    try:ipaddress.IPv4Address(ingress)
    except ValueError:raise BrokerError('IP ingress non configurée.')
    base='https://api.cloudflare.com/client/v4/zones/'+zone+'/dns_records'
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    # Strictly refuse replacing a record created by another administrator.
    req=Request(base+'?'+urlencode({'type':'A','name':host}),headers=headers)
    with urllib.request.urlopen(req,timeout=12) as resp: data=json.load(resp)
    if not data.get('success'):raise BrokerError('Recherche DNS Cloudflare refusée.')
    results=data.get('result') or []
    if results:
        if len(results)!=1 or results[0].get('content')!=ingress or results[0].get('proxied'):
            raise BrokerError('Enregistrement Cloudflare existant différent : aucune modification.')
        return {'created':False}
    payload=json.dumps({'type':'A','name':host,'content':ingress,'ttl':120,'proxied':False}).encode()
    req=Request(base,data=payload,headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=12) as resp: data=json.load(resp)
    if not data.get('success'):raise BrokerError('Création DNS Cloudflare refusée.')
    return {'created':True}

def _nginx_config(host,port,tls=False):
    if type(port)!=int or not 1024<=port<=65535:raise BrokerError('Port upstream invalide.')
    safe_host=valid_hostname(host)
    if tls:
        cert='/etc/letsencrypt/live/'+safe_host
        return (f'server {{\n    listen 80;\n    server_name {safe_host};\n'
            '    location /.well-known/acme-challenge/ { root /var/lib/autokom/acme; }\n'
            '    location / { return 308 https://$host$request_uri; }\n}\n'
            f'server {{\n    listen 443 ssl;\n    server_name {safe_host};\n'
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
            '    location /.well-known/acme-challenge/ { root /var/lib/autokom/acme; }\n'
            '    location / { return 503; }\n}\n')

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
    owner=STATE/'domain-owner-'+host+'.txt'
    if owner.is_file() and owner.read_text(encoding='utf-8').strip()!=environment:
        raise BrokerError('Domaine déjà attribué à un autre environnement.')
    path=PROXY/name; symlink=ENABLED/name
    if path.exists() and (path.is_symlink() or not owner.is_file()):
        raise BrokerError('Vhost existant non géré par AUTOKOM : aucune modification.')
    WEBROOT.mkdir(parents=True,exist_ok=True)
    old=path.read_text() if path.exists() else None
    def publish(content):
        with tempfile.NamedTemporaryFile('w',dir=PROXY,delete=False,prefix='.autokom-',encoding='utf-8') as f:
            f.write(content); tmp=f.name
        os.chmod(tmp,0o644);os.replace(tmp,path)
        if not symlink.exists():symlink.symlink_to(path)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])
    try:
        publish(_nginx_config(host,state['port'],tls=False))
        # Certbot --keep-until-expiring reuses a currently valid certificate.
        if True:
            email=os.environ.get('AUTOKOM_ACME_EMAIL','')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise BrokerError('E-mail Certbot AUTOKOM absent.')
            run(['certbot','certonly','--webroot','-w',str(WEBROOT),
                 '-d',host,'--agree-tos','--keep-until-expiring','--non-interactive','--email',email],timeout=130)
        publish(_nginx_config(host,state['port'],tls=True))
        owner.write_text(environment,encoding='utf-8')
        os.chmod(owner,0o600)
    except Exception:
        if old is not None:path.write_text(old,encoding='utf-8')
        else:path.unlink(missing_ok=True);symlink.unlink(missing_ok=True)
        subprocess.run(['nginx','-t'],capture_output=True,timeout=10)
        subprocess.run(['systemctl','reload','nginx'],capture_output=True,timeout=15)
        raise
    return {'active':True,'hostname':host}

def handle(action,params):
    if not isinstance(params,dict):raise BrokerError('Paramètres invalides.')
    if action=='ping':
        try:jenkins_config();configured=True
        except BrokerError:configured=False
        return {'jenkins_configured':configured,'runtime':'docker-local','registry_required':False,'ha_ready':False}
    if action.startswith('jenkins.'):
        return jenkins_action(action,params)
    if action=='runtime.deploy':return _docker_deploy(params)
    if action=='runtime.rollback':return _rollback(params)
    if action=='runtime.commit':return _commit(params)
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
    if action=='domain.verify':
        return _check_dns(params.get('hostname'),params.get('token'),params.get('managed') is True)
    if action=='domain.dns.create':
        return _cloudflare_record(valid_hostname(params.get('hostname')))
    if action=='domain.activate':
        host=valid_hostname(params.get('hostname'))
        _check_dns(host,params.get('token'),params.get('managed') is True)
        return _proxy_apply(host,valid_id(params.get('environment_id')))
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
            # No exception traceback, request content or secret values in response.
            response={'ok':False,'error':str(exc) if isinstance(exc,BrokerError) else 'Erreur interne du service AUTOKOM.'}
        self.wfile.write((json.dumps(response,ensure_ascii=False)+'\n').encode())

class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=True

def main():
    if os.geteuid()!=0:raise SystemExit('Lancer par systemd, utilisateur root.')
    SOCKET.parent.mkdir(parents=True,exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    IMAGES.mkdir(parents=True,exist_ok=True,mode=0o750)
    with Server(str(SOCKET),Handler) as server:
        os.chown(SOCKET,0,pwd.getpwnam('autokom').pw_gid)
        os.chmod(SOCKET,0o660)
        server.serve_forever(poll_interval=0.4)

if __name__=='__main__':
    main()
