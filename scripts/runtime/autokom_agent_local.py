#!/usr/bin/env python3
"""Trusted Jenkins agent: fetch signed manifest, scan sources, export a local image.

Run on isolated builder WITHOUT Docker socket, root or Nginx access. Never use shell.
The build artifact is consumed by the privileged AUTOKOM broker only after success.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

UUID=re.compile(r'^[a-f0-9-]{36}$')
SHA=re.compile(r'^[a-f0-9]{40}$')
REPO=re.compile(r'^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$')


def fail(msg):
    print('AUTOKOM_LOG: '+msg,flush=True)
    raise SystemExit(2)


def stage(name,state):print(f'AUTOKOM_STAGE:{name}:{state}',flush=True)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def manifest():
    rid=os.getenv('AUTOKOM_RUN_ID','')
    key=os.getenv('AUTOKOM_RUNNER_SHARED_KEY','')
    base=os.getenv('AUTOKOM_API_BASE','').rstrip('/')
    parsed=urlsplit(base)
    if not UUID.fullmatch(rid) or not len(key)>=32 or parsed.scheme!='https' or not parsed.hostname or parsed.path not in ('','/'):
        fail('Configuration du runner manquante ou non sécurisée.')
    timestamp=str(int(time.time()))
    signature=hmac.new(key.encode(),f'autokom-runner:v1:{timestamp}:{rid}'.encode(),hashlib.sha256).hexdigest()
    req=Request(base+f'/api/internal/runner/deployments/{rid}/manifest/',headers={
        'X-Autokom-Runner-Time':timestamp,'X-Autokom-Runner-Signature':signature})
    try:
        with build_opener(NoRedirect()).open(req,timeout=15) as response:
            doc=json.loads(response.read(125000))
    except Exception:
        fail('Manifeste non disponible; vérifier les secrets du runner.')
    if doc.get('run_id')!=rid or not SHA.fullmatch(doc.get('commit_sha','')) or not REPO.fullmatch(doc.get('repository','')):
        fail('Manifeste invalide.')
    return doc


def execute(argv,timeout=1500):
    env={k:v for k,v in os.environ.items() if k not in ('AUTOKOM_RUNNER_SHARED_KEY',
         'JENKINS_API_TOKEN','GITHUB_TOKEN','GITHUB_APP_CLIENT_SECRET','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN')}
    try:
        subprocess.run(argv,check=True,timeout=timeout,env=env)
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
        fail('Commande du runner refusée; consulter les logs protégés Jenkins.')


def source(doc):
    workspace=Path(os.getenv('WORKSPACE','')).resolve()
    if not workspace.is_dir() or not (workspace/'.git').is_dir():fail('Source GitHub non présente.')
    relative=doc['build'].get('root','')
    if not isinstance(relative,str) or relative.startswith('/') or '\\' in relative or any(p in ('','.','..') for p in relative.split('/') if relative):
        fail('Répertoire source invalide.')
    path=(workspace/relative).resolve()
    if not path.is_relative_to(workspace) or not path.is_dir():fail('Répertoire de build introuvable.')
    return path


def scan(doc,kind):
    if doc.get('mode')!='DEVSECOPS':
        stage(kind,'SKIPPED');return
    path=source(doc);stage(kind,'RUNNING')
    if kind=='sast':
        rules=os.getenv('AUTOKOM_SEMGREP_RULES','/etc/autokom/semgrep-rules.yml')
        if not Path(rules).is_file():fail('Règles Semgrep approuvées absentes.')
        execute(['semgrep','scan','--metrics','off','--config',rules,'--error',str(path)])
    else:
        execute(['trivy','fs','--scanners','vuln,secret','--severity','HIGH,CRITICAL',
                 '--exit-code','1','--no-progress',str(path)])
    stage(kind,'SUCCESS')


def build(doc):
    path=source(doc)
    cfg=doc['build']
    if cfg['method'] not in ('DOCKERFILE','AUTO','STATIC'):
        fail('Buildpacks exigera un runtime adapté; seul Dockerfile/STATIC est activé sans registre.')
    if cfg.get('start_command') or cfg.get('build_command'):
        fail('Commande personnalisée non autorisée pour le runner Dockerfile actuel.')
    rid=doc['run_id']
    artifact_dir=Path(os.getenv('AUTOKOM_ARTIFACT_DIR','/var/lib/autokom/build-artifacts'))
    if not artifact_dir.is_dir() or artifact_dir.is_symlink():fail('Répertoire d’artefacts non préparé.')
    dest=artifact_dir/(rid+'.tar')
    if dest.exists() or dest.is_symlink():fail('Artefact déjà existant; pas de remplacement.')
    dockerfile=cfg.get('dockerfile') or 'Dockerfile'
    if not isinstance(dockerfile,str) or dockerfile.startswith('/') or '\\' in dockerfile or '..' in dockerfile.split('/'):
        fail('Dockerfile non autorisé.')
    file=(path/dockerfile).resolve()
    if not file.is_relative_to(path) or not file.is_file():
        fail('Dockerfile requis pour ce mode. Les sites statiques nécessitent une image de build approuvée.')
    stage('build','RUNNING')
    with tempfile.NamedTemporaryFile(dir=artifact_dir,prefix='.building-',suffix='.tar',delete=False) as tmp:
        temp=Path(tmp.name)
    try:
        execute(['buildctl','build','--frontend','dockerfile.v0',
                 '--local','context='+str(path),'--local','dockerfile='+str(file.parent),
                 '--opt','filename='+file.name,
                 '--output','type=docker,name=autokom-local:'+rid+',dest='+str(temp)],timeout=1800)
        if not 1024<=temp.stat().st_size<=8*1024**3:fail('Artefact Docker vide ou trop volumineux.')
        os.chmod(temp,0o640)
        os.rename(temp,dest)
    finally:
        temp.unlink(missing_ok=True)
    stage('build','SUCCESS')


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('metadata','sast','sca','build'):
        fail('Phase de build inconnue.')
    doc=manifest()
    if sys.argv[1]=='metadata':
        print(json.dumps({'repo':doc['repository'],'sha':doc['commit_sha']}))
    elif sys.argv[1] in ('sast','sca'):scan(doc,sys.argv[1])
    else:build(doc)

if __name__=='__main__':main()
