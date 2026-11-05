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
    env=os.environ.copy()
    env['ANSIBLE_CONFIG']=str(ROOT/'ansible/ansible.cfg')
    env['AUTOKOM_CHECK_MODE']='0'
    check=subprocess.run(args,env=env,capture_output=True,text=True,timeout=25)
    if check.returncode:raise SetupError('Validation Ansible échouée : '+check.stderr[-900:])
    print('  ✓ Playbook AUTOKOM runtime valide')
    print('  › Service root broker UNIX · worker Django non privilégié · BuildKit artefacts privés')
    print('  › Ne touche ni aux zones BIND, ni aux DNS dot.mg, ni aux sites Nginx existants.')
    if not apply:
        args=['ansible-playbook','-i',str(inventory),'--check',str(playbook)]
        check_env=env.copy()
        check_env['AUTOKOM_CHECK_MODE']='1'
        run_live(args,env=check_env,label='Prévisualisation runtime via Ansible',timeout=240)
        print('  ✓ Prévisualisation Ansible terminée (mode check, sans modification).')
        return 0
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


def isolated_agent_status(env):
    """Inspect only an isolated online node; an executor on controller is unsafe."""
    endpoint = '/computer/api/json?tree=computer%5BdisplayName,offline,temporarilyOffline,numExecutors,assignedLabels%5Bname%5D%5D'
    status, data = jenkins_request(env, 'GET', endpoint, content_type='application/json')
    if status != 200:
        raise SetupError('Impossible de vérifier les nœuds Jenkins avec ce compte de service.')
    try:
        computers = json.loads(data).get('computer', [])
    except (ValueError, TypeError):
        raise SetupError('Inventaire des nœuds Jenkins illisible.')
    if not isinstance(computers, list):
        raise SetupError('Inventaire des nœuds Jenkins invalide.')
    for node in computers:
        if not isinstance(node, dict):
            continue
        if node.get('displayName') in ('Jenkins', 'Built-In Node', 'master', ''):
            continue
        labels = {label.get('name') for label in node.get('assignedLabels', [])
                  if isinstance(label, dict)}
        if 'autokom-isolated' not in labels:
            continue
        if (node.get('offline') is False and node.get('temporarilyOffline') is not True
                and type(node.get('numExecutors')) is int and node['numExecutors'] > 0):
            return True, 'online'
        return False, 'offline'
    return False, 'missing'


def jenkins_view_xml(job):
    """Create an optional aggregate List View; never modify other views/jobs."""
    root = ET.Element('hudson.model.ListView')
    ET.SubElement(root, 'name').text = 'AUTOKOM-Clients'
    ET.SubElement(root, 'description').text = 'Exécutions clientes AUTOKOM (job mutualisé)'
    ET.SubElement(root, 'filterExecutors').text = 'false'
    ET.SubElement(root, 'filterQueue').text = 'true'
    ET.SubElement(root, 'properties', {'class': 'hudson.model.View$PropertyList'})
    ET.SubElement(root, 'jobNames', {'class':'tree-set'})
    ET.SubElement(root, 'jobFilters')
    ET.SubElement(root, 'includeRegex').text = '^' + re.escape(job) + '$'
    ET.SubElement(root, 'recurse').text = 'false'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def jenkins_view_sync(env, job):
    """Best effort: do not compromise working jobs because a view is unsupported."""
    from urllib.parse import quote
    name = 'AUTOKOM-Clients'
    status, _ = jenkins_request(env, 'GET', '/view/' + name + '/api/json', content_type='application/json')
    if status == 200:
        print('  ✓ Vue Jenkins AUTOKOM-Clients déjà présente.')
        return
    if status != 404:
        print('  ! Vue Jenkins : droits de consultation insuffisants (sans impact sur le job).')
        return
    code, _ = jenkins_request(env, 'POST', '/createView?' + urlencode({'name':name}),
                              jenkins_view_xml(job))
    if code in (200, 201, 202, 302):
        print('  ✓ Vue Jenkins AUTOKOM-Clients créée.')
    else:
        print('  ! Vue Jenkins non créée ; vérifier le droit View/Create (sans impact sur le job).')


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
            print('  ✓ Job Jenkins déjà synchronisé.')
            if apply:
                jenkins_view_sync(env, job)
            ready, reason = isolated_agent_status(env)
            if not ready:
                print('  ! Agent autokom-isolated non disponible ('+reason+'). Le job ne démarrera pas.')
            return 0
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
    jenkins_view_sync(env, job)
    ready, reason = isolated_agent_status(env)
    if not ready:
        print('  ! Agent autokom-isolated non disponible ('+reason+'). Configuration du nœud requise.')
    return 0


def doctor():
    """Read-only end-to-end check through the exact Unix identity used by Django."""
    import shutil
    import stat
    import socket
    sock = Path('/run/autokom/platform-control.sock')
    print('  ◈ AUTOKOM · Diagnostic de liaison interne')
    failures=[]
    for unit in ('autokom-platform-broker.service','autokom-deployment-worker.service','jenkins.service'):
        state=systemctl_state(unit)
        print(f'  {"✓" if state == "active" else "✗"} {unit} : {state}')
        if state != 'active':failures.append(unit)
    directory = sock.parent
    if not directory.is_dir() or directory.is_symlink():
        raise SetupError('Répertoire de contrôle Unix absent ou non conforme.')
    import grp
    try:
        expected_gid = grp.getgrnam('autokom').gr_gid
    except KeyError:
        raise SetupError('Groupe de service autokom absent.')
    directory_stat = directory.stat()
    directory_mode = stat.S_IMODE(directory_stat.st_mode)
    if directory_stat.st_uid != 0 or directory_stat.st_gid != expected_gid or directory_mode != 0o750:
        print('  ✗ Répertoire de contrôle : attendu root:autokom (0750).')
        raise SetupError('Répertoire de contrôle non accessible au compte Django ; corriger via runtime setup --apply.')
    print('  ✓ Répertoire de contrôle : root:autokom (0750).')
    if not sock.exists() or not stat.S_ISSOCK(sock.stat().st_mode):
        raise SetupError('Le socket de contrôle n’existe pas. Vérifier le service broker.')
    sock_stat = sock.stat()
    if sock_stat.st_gid != expected_gid or stat.S_IMODE(sock_stat.st_mode) != 0o660:
        raise SetupError('Socket Unix : groupe ou permissions non conformes (attendu autokom, 0660).')
    print('  ✓ Socket Unix : groupe autokom, mode 0660.')
    if not shutil.which('runuser'):
        raise SetupError('La commande système runuser est absente.')
    probe = r"""import json,socket,sys
try:
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(4)
        s.connect('/run/autokom/platform-control.sock')
        s.sendall(b'{"action":"ping","params":{}}\n')
        b=b''
        while not b.endswith(b'\n') and len(b)<4096:
            v=s.recv(4096)
            if not v:break
            b+=v
    r=json.loads(b)
    if not r.get('ok') or not isinstance(r.get('result'),dict):
        print('diagnostic=protocol');sys.exit(3)
    print('jenkins_configured='+str(r['result'].get('jenkins_configured') is True).lower())
except PermissionError:
    print('diagnostic=permission');sys.exit(4)
except (FileNotFoundError,ConnectionRefusedError):
    print('diagnostic=socket');sys.exit(5)
except TimeoutError:
    print('diagnostic=timeout');sys.exit(6)
except (OSError,ValueError,KeyError,TypeError):
    print('diagnostic=protocol');sys.exit(7)
"""
    try:
        result=subprocess.run(['runuser','-u','autokom','--','/usr/bin/python3','-c',probe],
                              capture_output=True,text=True,timeout=9)
    except (OSError,subprocess.TimeoutExpired):
        raise SetupError('Impossible de tester la liaison avec le compte Django.')
    if result.returncode:
        categories = {
            'diagnostic=permission': 'Accès Unix refusé au compte Django.',
            'diagnostic=socket': 'Socket Unix absent ou connexion refusée.',
            'diagnostic=timeout': 'Broker joignable mais sans réponse dans le délai.',
            'diagnostic=protocol': 'Réponse du broker invalide ou opération refusée.',
        }
        reason = next((value for key, value in categories.items() if key in result.stdout),
                      'Sonde du compte Django impossible à exécuter.')
        print('  ✗ '+reason)
        raise SetupError('Liaison Django → broker non opérationnelle ; vérifier le journal du broker.')
    print('  ✓ Le compte Django communique réellement avec le broker.')
    if 'jenkins_configured=true' not in result.stdout:
        raise SetupError('Le broker fonctionne mais sa configuration Jenkins est incomplète.')
    print('  ✓ Identifiants Jenkins présents dans le broker.')
    env=load_env()
    job=env.get('AUTOKOM_JENKINS_JOB','autokom-client-deploy')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',job):
        raise SetupError('Le job Jenkins configuré porte un nom invalide.')
    try:
        from urllib.parse import quote
        status,_=jenkins_request(env,'GET','/job/'+quote(job,safe='')+'/api/json')
    except SetupError:
        raise SetupError('Jenkins refuse la connexion du compte de service ou est injoignable.')
    if status!=200:
        raise SetupError('Le job Jenkins n’est pas accessible au compte de service (code HTTP '+str(status)+').')
    print('  ✓ Job Jenkins accessible avec les identifiants du runtime.')
    ready, reason = isolated_agent_status(env)
    if not ready:
        print('  ✗ Agent autokom-isolated : '+reason)
        raise SetupError('Agent de compilation isolé absent ou hors ligne. N’ajouter aucun label au contrôleur Jenkins.')
    print('  ✓ Agent autokom-isolated connecté avec au moins un exécuteur.')
    if failures:
        raise SetupError('Un ou plusieurs services ne sont pas actifs.')
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
    sub.add_parser('doctor', help='Vérifier la liaison Django → socket → broker sans modifier le serveur')
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
    if opts.action=='doctor':return doctor()
    if opts.action=='setup':return setup(apply=opts.apply)
    return jenkins_sync(apply=opts.apply)

if __name__=='__main__':
    try:raise SystemExit(main())
    except SetupError as exc:raise SystemExit('  ✗ '+str(exc))
