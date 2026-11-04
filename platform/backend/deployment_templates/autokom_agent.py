#!/usr/bin/env python3
"""TRUSTED Jenkins agent script; install outside untrusted repositories.

NOT part of web backend execution. Runs on an isolated, access-controlled Jenkins
agent with rootless BuildKit/Buildpacks and namespace-scoped Kubernetes RBAC.
No user's source code or .env is executed on the AUTOKOM Django server.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SHA = re.compile(r'^[a-f0-9]{40}$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
UUID = re.compile(r'^[0-9a-fA-F-]{36}$')
REGISTRY = re.compile(r'^[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.-]+)*$')
KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def fatal(message):
    print('AUTOKOM_LOG: ' + message, flush=True)
    raise SystemExit(2)


def stage(name, status):
    print(f'AUTOKOM_STAGE:{name}:{status}', flush=True)


def read_manifest():
    run_id = os.environ.get('AUTOKOM_RUN_ID','')
    key = os.environ.get('AUTOKOM_RUNNER_SHARED_KEY','')
    url = os.environ.get('AUTOKOM_API_BASE','').rstrip('/')
    parsed = urlsplit(url)
    if (not UUID.fullmatch(run_id) or len(key) < 32 or parsed.scheme != 'https' or
            parsed.path not in ('','/') or parsed.username or parsed.password or not parsed.hostname):
        fatal('Configuration du runner incomplète ou non sécurisée.')
    timestamp = int(time.time())
    sig = hmac.new(key.encode(),f'autokom-runner:v1:{timestamp}:{run_id}'.encode(),hashlib.sha256).hexdigest()
    request = urllib.request.Request(url + f'/api/internal/runner/deployments/{run_id}/manifest/', headers={
        'X-Autokom-Runner-Time': str(timestamp), 'X-Autokom-Runner-Signature': sig,
        'Accept': 'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                fatal('Manifest non autorisé.')
            doc = json.loads(response.read(120000))
    except Exception:
        fatal('Impossible de récupérer le manifest signé du backend.')
    if doc.get('run_id') != run_id or not REPO.fullmatch(doc.get('repository','')) or not SHA.fullmatch(doc.get('commit_sha','')):
        fatal('Manifest GitHub invalide.')
    return doc


def workdir(manifest):
    workspace = Path(os.environ.get('WORKSPACE','')).resolve()
    if not workspace.is_dir() or not (workspace / '.git').exists():
        fatal('Workspace Git vérifié absent.')
    root = manifest['build']['root']
    if root and (root.startswith('/') or '\\' in root or any(x in ('','.','..') for x in root.split('/'))):
        fatal('Répertoire source non autorisé.')
    target = (workspace / root).resolve()
    if not target.is_relative_to(workspace) or not target.is_dir():
        fatal('Répertoire source introuvable.')
    return target


def run(command, *, stdin=None):
    # args list only, no shell. Never print args if they could contain secrets.
    # Never pass the control-plane HMAC or Jenkins credentials to build tooling.
    clean_env = {k:v for k,v in os.environ.items() if k not in (
        'AUTOKOM_RUNNER_SHARED_KEY', 'JENKINS_API_TOKEN', 'GITHUB_TOKEN',
        'GITHUB_APP_CLIENT_SECRET', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')}
    try:
        subprocess.run(command, input=stdin, text=True, env=clean_env,
                       check=True, timeout=1200)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        fatal('Étape runner échouée. Diagnostic réservé au journal Jenkins protégé.')


def image_ref(doc):
    registry = os.environ.get('AUTOKOM_IMAGE_REGISTRY','')
    if not REGISTRY.fullmatch(registry):
        fatal('Registry privé du runner non configuré.')
    return f"{registry.rstrip('/')}/{doc['project_id'].replace('-', '')}/{doc['run_id'].replace('-', '')}"


def scan(doc, kind):
    if doc['mode'] == 'DEVOPS':
        stage(kind, 'SKIPPED')
        return
    path = workdir(doc)
    stage(kind, 'RUNNING')
    if kind == 'sast':
        rules = os.environ.get('AUTOKOM_SEMGREP_RULES','/etc/autokom-runner/semgrep.yml')
        if not Path(rules).is_file():
            fatal('Règles Semgrep locales manquantes.')
        run(['semgrep','scan','--config',rules,'--metrics','off','--error',str(path)])
    else:
        run(['trivy','fs','--scanners','vuln,secret','--severity','HIGH,CRITICAL',
             '--exit-code','1','--no-progress',str(path)])
    stage(kind, 'SUCCESS')


def build(doc):
    stage('build','RUNNING')
    path = workdir(doc)
    build = doc['build']
    image = image_ref(doc)
    method = build['method']
    # Custom build commands are never executed on a privileged Jenkins host.
    script = (build.get('build_command') or '').strip()
    env = os.environ.copy()
    if method == 'DOCKERFILE' or (method == 'AUTO' and (path / build['dockerfile']).is_file()):
        if script:
            fatal('Avec Dockerfile, placer les commandes de build dans le Dockerfile lui-même.')
        dockerfile = build.get('dockerfile','Dockerfile')
        if dockerfile.startswith('/') or '\\' in dockerfile or '..' in dockerfile.split('/'):
            fatal('Dockerfile hors racine interdit.')
        file = (path / dockerfile).resolve()
        if not file.is_relative_to(path) or not file.is_file():
            fatal('Dockerfile introuvable.')
        run(['buildctl','build','--frontend','dockerfile.v0',
             '--local',f'context={path}','--local',f'dockerfile={file.parent}',
             '--opt',f'filename={file.name}',
             '--output',f'type=image,name={image},push=true'])
    elif method in ('AUTO','BUILDPACKS'):
        args = ['pack','build',image,'--path',str(path),'--builder',
                os.environ.get('AUTOKOM_BUILDPACK_BUILDER','paketobuildpacks/builder-jammy-base'),'--publish']
        if script:
            m = re.fullmatch(r'(?:npm run|yarn|pnpm run) ([A-Za-z0-9:_-]{1,60})',script)
            if not m or build['runtime'] != 'NODE':
                fatal('Commande personnalisée non prise en charge par ce builder isolé.')
            args.extend(['--env',f'BP_NODE_RUN_SCRIPTS={m.group(1)}'])
        run(args)
    elif method == 'STATIC':
        if script:
            fatal('En mode STATIC, compiler le répertoire de sortie avant le build.')
        output = build.get('output_directory')
        if not output or output.startswith('/') or '..' in output.split('/'):
            fatal('output_directory invalide.')
        dist = (path / output).resolve()
        if not dist.is_relative_to(path) or not dist.is_dir():
            fatal('Dossier de sortie statique absent.')
        # Rootless BuildKit with an immutable trusted Dockerfile, no repository Dockerfile execution.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='autokom-static-') as tmp:
            from shutil import copytree
            copytree(dist, Path(tmp)/'dist')
            (Path(tmp)/'Dockerfile').write_text('FROM nginx:stable-alpine\nCOPY dist/ /usr/share/nginx/html/\n')
            run(['buildctl','build','--frontend','dockerfile.v0','--local',f'context={tmp}',
                 '--local',f'dockerfile={tmp}','--output',f'type=image,name={image},push=true'])
    else:
        fatal('Méthode de build non prise en charge.')
    if doc['mode'] == 'DEVSECOPS':
        # Container image scan supplements source SCA even if lockfiles are absent.
        run(['trivy','image','--scanners','vuln,secret','--severity','HIGH,CRITICAL',
             '--exit-code','1','--no-progress',image])
    stage('build','SUCCESS')


def k8s_details(doc):
    project = doc['project_id'].replace('-','')
    environment = doc['environment_id'].replace('-','')
    return 'autokom-' + environment[:20], 'app-' + environment[:12]


def kube_apply(data, namespace):
    # JSON over stdin: no secret values in command args or shell/history.
    run(['kubectl','--namespace',namespace,'apply','-f','-'], stdin=json.dumps(data))


def deploy(doc):
    stage('release','RUNNING')
    namespace, name = k8s_details(doc)
    image = image_ref(doc)
    values = doc.get('variables') or {}
    if any(not KEY.fullmatch(k) or not isinstance(v,str) or len(v)>32768 for k,v in values.items()):
        fatal('Variable d’environnement invalide.')
    resources = doc['resources']
    replicas = resources['replicas']
    if type(replicas) is not int or not 1 <= replicas <= 20:
        fatal('Nombre de réplicas invalide.')
    limit = {'cpu': str(resources['cpu_millicores'])+'m',
             'memory': str(resources['memory_mb'])+'Mi',
             'ephemeral-storage': str(resources['storage_mb'])+'Mi'}
    selectors = {'app.kubernetes.io/managed-by':'autokom', 'autokom.io/environment':name}
    secret_name = name+'-environment'
    secret = {'apiVersion':'v1','kind':'Secret', 'metadata':{'name':secret_name,'namespace':namespace},
              'type':'Opaque','data':{key:base64.b64encode(value.encode()).decode()
                                       for key,value in values.items()}}
    kube_apply(secret, namespace)
    container = {'name':'app','image':image,'imagePullPolicy':'Always',
                 'ports':[{'containerPort':doc['build']['port']}],
                 'envFrom':[{'secretRef':{'name':secret_name}}],
                 'resources':{'requests':{'cpu':str(max(100,int(resources['cpu_millicores'])*50//100))+'m',
                                          'memory':str(max(64,int(resources['memory_mb'])//2))+'Mi'},
                              'limits':limit},
                 'readinessProbe':{'httpGet':{'path':doc['build']['healthcheck'],
                                              'port':doc['build']['port']},
                                   'initialDelaySeconds':5,'periodSeconds':10},
                 'livenessProbe':{'httpGet':{'path':doc['build']['healthcheck'],
                                             'port':doc['build']['port']},
                                  'initialDelaySeconds':25,'periodSeconds':20}}
    if doc['build'].get('start_command'):
        container['command']=['/bin/sh','-lc',doc['build']['start_command']]
    pod_spec = {'containers':[container], 'automountServiceAccountToken': False}
    if replicas>1:
        pod_spec['affinity']={'podAntiAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':[
            {'labelSelector':{'matchLabels':selectors},'topologyKey':'kubernetes.io/hostname'}]}}
    deployment = {'apiVersion':'apps/v1','kind':'Deployment',
                  'metadata':{'name':name,'namespace':namespace,'labels':selectors},
                  'spec':{'replicas':replicas,'revisionHistoryLimit':3,
                          'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxUnavailable':0,'maxSurge':1}},
                          'selector':{'matchLabels':selectors},
                          'template':{'metadata':{'labels':selectors},'spec':pod_spec}}}
    svc = {'apiVersion':'v1','kind':'Service', 'metadata':{'name':name,'namespace':namespace},
           'spec':{'type':'ClusterIP','selector':selectors,
                   'ports':[{'name':'http','port':80,'targetPort':doc['build']['port']}]}}
    kube_apply(deployment, namespace)
    kube_apply(svc, namespace)
    # Public ingress is created only for domains already verified by the backend.
    domains = doc.get('domains') or []
    if domains:
        issuer = os.environ.get('AUTOKOM_CLUSTER_ISSUER','')
        ingress_class = os.environ.get('AUTOKOM_INGRESS_CLASS','nginx')
        if not issuer or not re.fullmatch(r'[a-z0-9-]{1,63}', issuer) or not re.fullmatch(r'[a-z0-9-]{1,63}',ingress_class):
            fatal('Cert-manager / ingress class non configurés pour le domaine public.')
        if len(domains)>10 or any(not re.fullmatch(r'(?:[a-z0-9-]{1,63}\.)+[a-z]{2,63}',h) for h in domains):
            fatal('Domaine d’accès invalide.')
        hosts = [{'host':host,'http':{'paths':[{'path':'/','pathType':'Prefix',
                    'backend':{'service':{'name':name,'port':{'number':80}}}}]}}
                 for host in domains]
        ingress = {'apiVersion':'networking.k8s.io/v1','kind':'Ingress',
            'metadata':{'name':name,'namespace':namespace,
                        'annotations':{'cert-manager.io/cluster-issuer':issuer}},
            'spec':{'ingressClassName':ingress_class,
                    'tls':[{'hosts':domains,'secretName':name+'-tls'}],
                    'rules':hosts}}
        kube_apply(ingress, namespace)
    stage('release','SUCCESS')


def health(doc):
    stage('health','RUNNING')
    namespace,name = k8s_details(doc)
    run(['kubectl','--namespace',namespace,'rollout','status','deployment/'+name,'--timeout=240s'])
    stage('health','SUCCESS')


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('metadata','sast','sca','build','deploy','health'):
        fatal('Phase runner invalide.')
    operation = sys.argv[1]
    doc=read_manifest()
    if operation=='metadata':
        print(json.dumps({'clone_url':f"https://github.com/{doc['repository']}.git",
                          'commit_sha':doc['commit_sha']}))
    elif operation in ('sast','sca'):
        scan(doc,operation)
    elif operation=='build':
        build(doc)
    elif operation=='deploy':
        deploy(doc)
    elif operation=='health':
        health(doc)

if __name__=='__main__':
    main()
