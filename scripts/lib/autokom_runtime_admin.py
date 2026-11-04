#!/usr/bin/env python3
"""Host-side AUTOKOM runtime lifecycle; runs ONLY through the AUTOKOM CLI as root."""
import argparse
from collections import deque
from datetime import datetime
import queue
import threading
import time
import sys
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from xml.etree import ElementTree as ET
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[2]
RUNTIME_ENV=Path('/etc/autokom/secrets/runtime.env')
BACKUP=Path('/var/lib/autokom/jenkins-backups')
JOB_SCRIPT=ROOT/'scripts/runtime/Jenkinsfile.autokom-local'

class SetupError(Exception):pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def load_env(path=RUNTIME_ENV):
    result={}
    if not path.is_file():return result
    # Only allow a single line; EnvironmentFile quoting is supported by systemd,
    # admins should use straightforward quoted strings when necessary.
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:continue
        k,v=line.split('=',1)
        if re.fullmatch(r'[A-Z][A-Z0-9_]*',k):
            result[k]=v.strip().strip('"').strip("'")
    return result


def confirm(keyword):
    if not os.isatty(0):raise SetupError('Confirmation interactive requise.')
    if input('  Taper '+keyword+' pour confirmer : ').strip()!=keyword:
        raise SetupError('Opération annulée.')


def systemctl_state(unit):
    p=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=8)
    return p.stdout.strip() or 'unknown'



def run_live(command, *, env, label, timeout=300):
    """Keep genuine tool output in a 0600 log, repaint last 3 lines under a spinner."""
    log_dir=Path('/var/log/autokom/runtime')
    log_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=log_dir/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())+'.log')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    items=queue.Queue()
    last=deque(maxlen=3)
    with os.fdopen(fd,'w',encoding='utf-8') as logfile:
        process=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,bufsize=1)
        def reader():
            for line in process.stdout:
                items.put(line)
            items.put(None)
        threading.Thread(target=reader,daemon=True).start()
        spinner='|/-\\'
        start=time.monotonic(); tty=sys.stdout.isatty(); opened=False; done=False
        while not done:
            try:
                value=items.get(timeout=0.15)
                if value is None:done=True
                else:
                    logfile.write(value); logfile.flush()
                    safe=''.join(c for c in value.strip() if c.isprintable())
                    if safe and not any(x in safe.lower() for x in ('password=','token=','secret=')):
                        last.append(safe[:125])
            except queue.Empty:
                pass
            if time.monotonic()-start>timeout:
                process.kill();process.wait(timeout=8)
                raise SetupError('Délai dépassé : consulter '+str(path))
            if tty:
                if opened:sys.stdout.write('\033[4A')
                elapsed=int(time.monotonic()-start)
                blocks=[f'  {spinner[elapsed%len(spinner)]} {label} · {elapsed}s']
                blocks.extend('    \033[32m│ '+x+'\033[0m' for x in list(last)[-3:])
                blocks+=['']*(4-len(blocks))
                sys.stdout.write(''.join('\r\033[K'+line+'\n' for line in blocks))
                sys.stdout.flush();opened=True
        code=process.wait(timeout=10)
        if tty:sys.stdout.write('\033[4A'+''.join('\r\033[K\n' for _ in range(4))+'\033[4A')
        print(f'  {"✓" if code==0 else "✗"} {label} ({int(time.monotonic()-start)}s)')
        print('  › Journal détaillé :',path)
        if code:
            for line in last:
                print('    │ '+line[:125])
            raise SetupError('Ansible a refusé une étape. Aucun contournement automatique.')

def setup(*,apply):
    playbook=ROOT/'ansible/playbooks/platform-runtime.yml'
    inventory=ROOT/'ansible/inventories/local/hosts.ini'
    args=['ansible-playbook','-i',str(inventory),'--syntax-check',str(playbook)]
    env=os.environ.copy();env['ANSIBLE_CONFIG']=str(ROOT/'ansible/ansible.cfg')
    check=subprocess.run(args,env=env,capture_output=True,text=True,timeout=25)
    if check.returncode:raise SetupError('Validation Ansible échouée : '+check.stderr[-900:])
    print('  ✓ Playbook AUTOKOM runtime valide')
    print('  › Service root broker UNIX · worker Django non privilégié · BuildKit artefacts privés')
    print('  › Ne touche ni aux zones BIND, ni aux DNS dot.mg, ni aux sites Nginx existants.')
    if not apply:
        print('  ✓ Prévisualisation uniquement');return 0
    if not RUNTIME_ENV.is_file():
        raise SetupError('Configuration absente : sudo autokom runtime env setup (enregistrement exclusivement sous /etc/autokom/secrets/).')
    if RUNTIME_ENV.stat().st_mode & 0o077:
        raise SetupError('Permissions trop ouvertes : chmod 600 /etc/autokom/secrets/runtime.env.')
    confirm('INSTALLER')
    args=['ansible-playbook','-i',str(inventory),str(playbook)]
    run_live(args,env=env,label='Fondation Docker / socket / worker via Ansible',timeout=240)
    print('  ✓ Broker:',systemctl_state('autokom-platform-broker.service'))
    print('  ✓ Worker:',systemctl_state('autokom-deployment-worker.service'))
    return 0


def jenkins_request(env,method,path,data=None,content_type='application/xml'):
    from urllib.parse import urlsplit
    url=env.get('AUTOKOM_JENKINS_URL','').rstrip('/')
    parsed=urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise SetupError('URL Jenkins invalide.')
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')):
        raise SetupError('Jenkins ne doit être accessible qu’en TLS ou en loopback.')
    job=env.get('AUTOKOM_JENKINS_JOB','autokom-client-deploy')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',job):raise SetupError('Nom de job invalide.')
    user=env.get('AUTOKOM_JENKINS_USER','');token=env.get('AUTOKOM_JENKINS_API_TOKEN','')
    if not user or not token:raise SetupError('Compte de service Jenkins manquant.')
    import base64
    auth=base64.b64encode(f'{user}:{token}'.encode()).decode()
    req=Request(url+path,data=data,method=method,headers={
        'Authorization':'Basic '+auth,'Content-Type':content_type})
    try:
        with build_opener(NoRedirect()).open(req,timeout=20) as resp:return resp.status,resp.read(128000)
    except HTTPError as exc:return exc.code,b''
    except (URLError,OSError,TimeoutError) as exc:raise SetupError('Jenkins inaccessible.') from exc


def pipeline_xml(script):
    root=ET.Element('flow-definition',{'plugin':'workflow-job'})
    ET.SubElement(root,'description').text='AUTOKOM trusted Docker local pipeline · NO REGISTRY · DO NOT CHANGE FROM UI'
    ET.SubElement(root,'keepDependencies').text='false'
    properties=ET.SubElement(root,'properties')
    params=ET.SubElement(properties,'hudson.model.ParametersDefinitionProperty')
    p=ET.SubElement(ET.SubElement(params,'parameterDefinitions'),'hudson.model.StringParameterDefinition')
    ET.SubElement(p,'name').text='AUTOKOM_RUN_ID';ET.SubElement(p,'description').text='UUID AUTOKOM seul'
    ET.SubElement(p,'defaultValue').text='';ET.SubElement(p,'trim').text='true'
    definition=ET.SubElement(root,'definition',{'class':'org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition','plugin':'workflow-cps'})
    ET.SubElement(definition,'script').text=script
    ET.SubElement(definition,'sandbox').text='true'
    ET.SubElement(root,'disabled').text='false'
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def jenkins_sync(*,apply):
    env=load_env()
    job=env.get('AUTOKOM_JENKINS_JOB','autokom-client-deploy')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',job):raise SetupError('Nom de job Jenkins invalide.')
    if not JOB_SCRIPT.is_file():raise SetupError('Jenkinsfile AUTOKOM approuvé absent.')
    source=JOB_SCRIPT.read_text(encoding='utf-8')
    if 'buildctl' not in source and 'autokom_agent_local.py' not in source:
        raise SetupError('Jenkinsfile approuvé incomplet.')
    status,old=jenkins_request(env,'GET','/job/'+job+'/config.xml')
    if status not in (200,404):raise SetupError('Impossible de lire le job Jenkins.')
    if status==200:
        try:
            existing=ET.fromstring(old).findtext('./definition/script') or ''
        except ET.ParseError:raise SetupError('Job Jenkins XML illisible; aucune modification.')
        if existing==source:
            print('  ✓ Job Jenkins déjà synchronisé.');return 0
    print(f"  ◈ Jenkins · {'mise à jour' if status==200 else 'création'} du job {job}")
    print('  › Pipeline approuvé AUTOKOM, image locale, scan DevSecOps, runner rootless')
    print('  › Agent requis : autokom-isolated, sans appartenance au groupe docker')
    if not apply:
        print('  ✓ Prévisualisation uniquement');return 0
    confirm('SYNCHRONISER')
    if status==200:
        BACKUP.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=BACKUP/(job+'-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'.xml')
        with target.open('xb') as f:f.write(old)
        os.chmod(target,0o600)
    if status==404:
        path='/createItem?'+urlencode({'name':job})
    else:path='/job/'+job+'/config.xml'
    code,_=jenkins_request(env,'POST',path,pipeline_xml(source))
    if code not in (200,201,202,302):
        raise SetupError('Synchronisation refusée par Jenkins; sauvegarde conservée.')
    print('  ✓ Jenkins pipeline local synchronisé.')
    return 0


def main(argv=None):
    # Delegate nested help and subcommands before the parent parser handles -h.
    raw=list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0]=='env':
        from autokom_env_manager import main as env_main, EnvError
        try:return env_main('runtime',raw[1:])
        except EnvError as exc:raise SetupError(str(exc)) from exc
    parser=argparse.ArgumentParser(prog='autokom runtime',description='Runtime client AUTOKOM Docker/Jenkins/socket')
    sub=parser.add_subparsers(dest='action',required=True)
    for item in ('setup','jenkins'):
        p=sub.add_parser(item)
        group=p.add_mutually_exclusive_group()
        group.add_argument('--check',action='store_true')
        group.add_argument('--apply',action='store_true')
    sub.add_parser('status')
    env_p = sub.add_parser('env', help='Secrets runtime : show, setup, edit, delete',add_help=False)
    env_p.add_argument('env_args', nargs=argparse.REMAINDER)
    opts=parser.parse_args(argv)
    if opts.action=='env':
        from autokom_env_manager import main as env_main, EnvError
        try: return env_main('runtime', opts.env_args)
        except EnvError as exc: raise SetupError(str(exc)) from exc
    if opts.action=='status':
        print('  ◈ AUTOKOM · RUNTIME CLIENT')
        for unit in ('autokom-platform-broker.service','autokom-deployment-worker.service'):
            print('  ›',unit,systemctl_state(unit))
        print('  › Configuration :', 'présente' if RUNTIME_ENV.is_file() else 'à créer')
        return 0
    if os.geteuid():raise SetupError('Utiliser sudo autokom runtime '+opts.action)
    if opts.action=='setup':return setup(apply=opts.apply)
    return jenkins_sync(apply=opts.apply)

if __name__=='__main__':
    try:raise SystemExit(main())
    except SetupError as exc:raise SystemExit('  ✗ '+str(exc))
