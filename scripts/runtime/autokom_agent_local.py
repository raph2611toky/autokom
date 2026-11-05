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
    if cfg['method'] not in ('DOCKERFILE','STATIC'):
        fail('Buildpacks et AUTO ne sont pas encore pris en charge.')
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
    if cfg['method'] == 'DOCKERFILE':
        file=(path/dockerfile).resolve()
        if not file.is_relative_to(path) or not file.is_file():
            fail('Dockerfile introuvable pour le build demandé.')
    else:
        if cfg.get('runtime') != 'STATIC' or cfg.get('output_directory') != 'dist' or cfg.get('port') != 8080:
            fail('Configuration Vite statique invalide.')
        package = path/'package.json'
        lockfile = path/'package-lock.json'
        if package.is_symlink() or lockfile.is_symlink() or not package.is_file() or not lockfile.is_file():
            fail('Vite nécessite package.json et package-lock.json dans le dossier source.')
        try:
            if package.stat().st_size > 65536: fail('Manifeste npm trop volumineux.')
            parsed = json.loads(package.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            fail('package.json non valide.')
        deps = {**(parsed.get('dependencies') or {}), **(parsed.get('devDependencies') or {})}
        if 'vite' not in deps or 'build' not in (parsed.get('scripts') or {}):
            fail('Projet Vite avec script build requis pour ce build gratuit.')
        # Base images are provisioned by BuildKit and should be digest-pinned in production.
        # Only the trusted AUTOKOM recipe is executed, never an arbitrary user path.
        recipe = (
            'FROM node:22-alpine AS builder\n'
            'WORKDIR /app\n'
            'COPY package.json package-lock.json ./\n'
            'RUN npm ci --ignore-scripts --no-audit --no-fund\n'
            'COPY . .\n'
            'RUN npm run build\n'
            'RUN printf "%s\\n" "server {" "listen 8080;" "server_name _;" '
            '"root /usr/share/nginx/html;" "index index.html;" '
            '"location / { try_files \\$uri \\$uri/ /index.html; }" '
            '"add_header X-Content-Type-Options nosniff always;" "}" > /tmp/autokom-nginx.conf\n'
            'FROM nginxinc/nginx-unprivileged:1.29-alpine\n'
            'COPY --from=builder /tmp/autokom-nginx.conf /etc/nginx/conf.d/default.conf\n'
            'COPY --from=builder /app/dist/ /usr/share/nginx/html/\n'
            'EXPOSE 8080\n'
        )
    stage('build','RUNNING')
    with tempfile.NamedTemporaryFile(dir=artifact_dir,prefix='.building-',suffix='.tar',delete=False) as tmp:
        temp=Path(tmp.name)
    try:
        if cfg['method'] == 'STATIC':
            with tempfile.TemporaryDirectory(prefix='autokom-vite-dockerfile-') as trusted_dir:
                trusted = Path(trusted_dir)/'Dockerfile'
                trusted.write_text(recipe, encoding='utf-8')
                execute(['buildctl','build','--frontend','dockerfile.v0',
                         '--local','context='+str(path),'--local','dockerfile='+trusted_dir,
                         '--opt','filename=Dockerfile',
                         '--output','type=docker,name=autokom-local:'+rid+',dest='+str(temp)],timeout=1800)
        else:
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
