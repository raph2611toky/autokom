#!/usr/bin/env python3
"""Trusted Jenkins agent: fetch signed manifest, scan sources, export a local image.

Run on isolated builder WITHOUT Docker socket, root or Nginx access. Never use shell.
The build artifact is consumed by the privileged AUTOKOM broker only after success.
"""
import hashlib
import hmac
import json
import io
import zipfile
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request,build_opener,HTTPRedirectHandler
# The runner is loaded both as a Jenkins script and through importlib in tests.
_RUNTIME_DIR = str(Path(__file__).resolve().parent)
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)
from autokom_backend_recipes import (BACKEND_PROFILES, BACKEND_RUNTIME, backend_recipe, validate_backend_source)
# The policy is pure Python and shared with Django; never import a client module.
_BACKEND_PY = Path(__file__).resolve().parents[2] / "platform" / "backend"
if not (_BACKEND_PY / "apps" / "projects" / "git" / "sqlite_policy.py").is_file():
    raise RuntimeError("AUTOKOM SQLite policy missing: backend source deployment incomplete")
if str(_BACKEND_PY) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PY))
from apps.projects.git.sqlite_policy import sqlite_storage_warning
from autokom_frontend_recipes import (ALL_PROFILES, STATIC_PROFILES, recipe as frontend_recipe,
    static_server_config, validate_profile_source)

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
    if (doc.get('run_id')!=rid or not SHA.fullmatch(doc.get('commit_sha',''))
            or not REPO.fullmatch(doc.get('repository',''))
            or doc.get('mode') not in ('DEVOPS','DEVSECOPS')):
        fail('Manifeste invalide ou mode de pipeline non reconnu.')
    return doc


def buildkit_preflight():
    """Fail closed with an actionable, non-secret reason before building.

    A client must never be given access to the host Docker socket or rootful
    BuildKit socket. The isolated agent needs its own UNIX BuildKit endpoint.
    """
    if not shutil.which('buildctl'):
        fail('Compilation indisponible : client BuildKit absent sur le serveur.')
    endpoint = os.getenv('BUILDKIT_HOST', '').strip()
    if not endpoint.startswith('unix:///') or '?' in endpoint or '#' in endpoint:
        fail('Compilation indisponible : socket BuildKit isolé non configuré.')
    address = endpoint[len('unix://'):]
    if not address.startswith('/') or '\x00' in address:
        fail('Compilation indisponible : adresse BuildKit locale invalide.')
    path = Path(address)
    # Never permit the privileged daemon's default socket as a fallback.
    allowed = Path('/run/autokom-buildkit/buildkitd.sock')
    if path != allowed:
        fail('Compilation indisponible : socket BuildKit non isolé.')
    try:
        if path.is_symlink() or not stat.S_ISSOCK(path.stat().st_mode):
            fail('Compilation indisponible : socket BuildKit invalide.')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(address)
    except FileNotFoundError:
        fail('Compilation indisponible : socket BuildKit absent, service à provisionner.')
    except (PermissionError, ConnectionRefusedError, TimeoutError, OSError):
        fail('Compilation indisponible : service BuildKit isolé inaccessible.')


def execute(argv,timeout=1500):
    env={k:v for k,v in os.environ.items() if k not in ('AUTOKOM_RUNNER_SHARED_KEY',
         'JENKINS_API_TOKEN','GITHUB_TOKEN','GITHUB_APP_CLIENT_SECRET','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN')}
    if argv and argv[0] == 'trivy':
        # Keep Jenkins scans out of root's shared cache and Django's private tree.
        env['TRIVY_CACHE_DIR'] = '/var/lib/autokom-jenkins-agent-v2/.cache/trivy'
        env['XDG_CACHE_HOME'] = '/var/lib/autokom-jenkins-agent-v2/.cache'
        # Do not load configuration from the current working directory.
        # Jenkins sources can contain untrusted trivy.yaml, .trivyignore and
        # trivy-secret.yaml files. They must not override security policy.
        # Explicit empty CLI flags are required: empty env vars are ignored.
        if len(argv) > 1 and argv[1] in ('fs', 'filesystem'):
            argv = [*argv[:2], '--config=', '--ignorefile=',
                    '--secret-config=', *argv[2:]]
        else:
            argv = [argv[0], '--config=', *argv[1:]]
    try:
        subprocess.run(argv,check=True,timeout=timeout,env=env)
    except FileNotFoundError:
        fail('Commande approuvée indisponible sur le serveur.')
    except PermissionError:
        fail('Exécution refusée par les permissions de l’agent isolé.')
    except subprocess.TimeoutExpired:
        fail('Étape de compilation expirée; vérifier les journaux administrateur.')
    except subprocess.CalledProcessError as exc:
        # Raw build output remains in Jenkins, not exposed through the client API.
        if argv and argv[0] == 'buildctl':
            fail('Compilation échouée (BuildKit, code retour %d). Vérifier le journal administrateur.' % exc.returncode)
        fail('Analyse refusée (code retour %d). Vérifier le journal administrateur.' % exc.returncode)
    except OSError:
        fail('Commande du runner indisponible; vérifier le serveur.')


SOURCE_ARCHIVE_LIMIT = 128 * 1024 * 1024
SOURCE_EXPANDED_LIMIT = 512 * 1024 * 1024
SOURCE_FILE_LIMIT = 128 * 1024 * 1024
SOURCE_MEMBER_LIMIT = 20000
SOURCE_MARKER = '.autokom-source.json'


def _runner_headers(doc):
    rid = doc.get('run_id', '')
    key = os.getenv('AUTOKOM_RUNNER_SHARED_KEY', '')
    if not UUID.fullmatch(rid) or len(key) < 32:
        fail('Authentification du runner GitHub indisponible.')
    epoch = str(int(time.time()))
    sig = hmac.new(key.encode(), f'autokom-runner:v1:{epoch}:{rid}'.encode(), hashlib.sha256).hexdigest()
    return {'X-Autokom-Runner-Time': epoch, 'X-Autokom-Runner-Signature': sig}


def _archive_parts(archive):
    """Validate all paths/attributes BEFORE extracting untrusted GitHub bytes."""
    names = set()
    parts = []
    root = None
    total = 0
    for info in archive.infolist():
        raw = info.filename
        if len(parts) >= SOURCE_MEMBER_LIMIT or not raw or len(raw) > 800:
            fail('Archive GitHub trop volumineuse ou chemin invalide.')
        if raw.startswith('/') or '\\' in raw or '\x00' in raw or '//' in raw:
            fail('Chemin GitHub non sécurisé.')
        segments = raw.rstrip('/').split('/')
        if not segments or any(part in ('', '.', '..') for part in segments):
            fail('Archive GitHub contient une traversée de chemin.')
        if root is None:
            root = segments[0]
        if segments[0] != root:
            fail('Structure d’archive GitHub invalide.')
        # GitHub zipballs normally start with one top-level directory entry.
        if len(segments) == 1 and info.is_dir():
            continue
        if len(segments) < 2 or segments[-1] == SOURCE_MARKER:
            # The source marker is generated by AUTOKOM and must not come from git.
            fail('Structure d’archive GitHub invalide.')
        if any(part == '.git' for part in segments):
            fail('Archive GitHub avec métadonnées Git non autorisées.')
        rel = '/'.join(segments[1:])
        if rel in names:
            fail('Fichier dupliqué dans l’archive GitHub.')
        names.add(rel)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            fail('Archive GitHub contenant un lien ou fichier spécial.')
        if info.file_size > SOURCE_FILE_LIMIT:
            fail('Fichier source trop volumineux.')
        total += info.file_size
        if total > SOURCE_EXPANDED_LIMIT:
            fail('Sources GitHub trop volumineuses.')
        parts.append((info, segments[1:]))
    if not parts:
        fail('Archive GitHub vide.')
    return parts


def _unpack_private_source(data, workspace, doc):
    """Extract a pinned snapshot without executing customer code or trusting symlinks."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            checked = _archive_parts(archive)
            for info, segments in checked:
                target = workspace.joinpath(*segments)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                count = 0
                with archive.open(info) as src, target.open('xb') as dest:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > SOURCE_FILE_LIMIT:
                            fail('Archive GitHub : dépassement de la taille déclarée.')
                        dest.write(chunk)
                os.chmod(target, 0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
    except (OSError, ValueError, zipfile.BadZipFile, EOFError):
        fail('Archive GitHub invalide ou inaccessible.')
    marker = workspace / SOURCE_MARKER
    marker.write_text(json.dumps({'run_id': doc['run_id'], 'commit_sha': doc['commit_sha']}), encoding='utf-8')
    os.chmod(marker, 0o600)


def private_source(doc):
    """Fetch a single commit via Django; no GitHub OAuth credential on Jenkins."""
    workspace_raw = os.getenv('WORKSPACE', '')
    if not workspace_raw:
        fail('Espace Jenkins absent.')
    workspace = Path(workspace_raw)
    if workspace.is_symlink() or not workspace.is_dir():
        fail('Espace Jenkins non conforme.')
    if any(workspace.iterdir()):
        fail('Espace Jenkins non vide : nettoyage isolé requis.')
    base = os.getenv('AUTOKOM_API_BASE', '').rstrip('/')
    uri = urlsplit(base)
    if (uri.scheme != 'https' or not uri.hostname or uri.username or uri.password
            or uri.path not in ('', '/') or uri.query or uri.fragment):
        fail('URL du backend AUTOKOM non conforme.')
    url = base + f"/api/internal/runner/deployments/{doc['run_id']}/source/"
    req = Request(url, headers=_runner_headers(doc))
    try:
        with build_opener(NoRedirect()).open(req, timeout=150) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/zip':
                fail('Dépôt GitHub privé non autorisé. Vérifier la connexion GitHub App et Contents: read.')
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as data:
                count = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    count += len(block)
                    if count > SOURCE_ARCHIVE_LIMIT:
                        fail('Archive GitHub dépassant la limite autorisée.')
                    data.write(block)
                data.seek(0)
                _unpack_private_source(data.read(), workspace, doc)
    except HTTPError as exc:
        if exc.code in (401, 403, 404):
            fail('GitHub App : dépôt privé non autorisé. Reconnecter le compte ou autoriser Contents: read.')
        fail('Service de récupération GitHub indisponible (HTTP %d).' % exc.code)
    except (URLError, OSError, TimeoutError, ValueError):
        fail('Téléchargement GitHub privé indisponible, vérifier l’autorisation du dépôt.')
    print('AUTOKOM_TECH:Commit privé récupéré via GitHub App, sans credential Jenkins permanent.', flush=True)


def source(doc):
    workspace=Path(os.getenv('WORKSPACE','')).resolve()
    if not workspace.is_dir():fail('Source GitHub non présente.')
    if not (workspace/'.git').is_dir():
        try:
            marker = json.loads((workspace/SOURCE_MARKER).read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            fail('Source GitHub non vérifiée.')
        if marker != {'run_id': doc.get('run_id'), 'commit_sha': doc.get('commit_sha')}:
            fail('Source GitHub ne correspond pas au déploiement demandé.')
    relative=doc['build'].get('root','')
    if not isinstance(relative,str) or relative.startswith('/') or '\\' in relative or any(p in ('','.','..') for p in relative.split('/') if relative):
        fail('Répertoire source invalide.')
    path=(workspace/relative).resolve()
    if not path.is_relative_to(workspace) or not path.is_dir():fail('Répertoire de build introuvable.')
    return path


def scan(doc,kind):
    mode=doc.get('mode')
    if mode=='DEVOPS':
        stage(kind,'SKIPPED');print('AUTOKOM_TECH:Mode DevOps : scanner non requis.',flush=True);return
    if mode!='DEVSECOPS' or kind not in ('sast','sca'):
        fail('Mode DevSecOps ou type de scanner invalide.')
    path=source(doc);stage(kind,'RUNNING')
    tool='semgrep' if kind=='sast' else 'trivy'
    try:
        if not shutil.which(tool):
            fail('DevSecOps indisponible : scanner '+tool+' absent du builder isolé.')
        if kind=='sast':
            rules=os.getenv('AUTOKOM_SEMGREP_RULES',
                '/usr/local/share/autokom-security/client-semgrep-rules.yml')
            if not Path(rules).is_file() or not os.access(rules,os.R_OK):
                fail('DevSecOps indisponible : règles Semgrep approuvées illisibles.')
            print('AUTOKOM_TECH:DevSecOps : analyse SAST Semgrep en cours.',flush=True)
            execute(['semgrep','scan','--metrics','off','--config',rules,'--error',str(path)])
        else:
            print('AUTOKOM_TECH:DevSecOps : analyse SCA et secrets Trivy en cours.',flush=True)
            execute(['trivy','fs','--scanners','vuln,secret','--severity','HIGH,CRITICAL',
                     '--exit-code','1','--no-progress',str(path)])
    except SystemExit:
        stage(kind,'FAILED')
        raise
    stage(kind,'SUCCESS')



def public_vite_build_args(doc):
    """The runner accepts only values explicitly marked as public by Django.

    Never interpolate values into Dockerfile source or execute them through a shell.
    """
    values = doc.get('public_build_variables', {})
    if not isinstance(values, dict) or len(values) > 40:
        fail('Configuration de variables Vite publiques invalide.')
    args = []
    declarations = []
    for key, value in sorted(values.items()):
        if not re.fullmatch(r'VITE_[A-Z][A-Z0-9_]{0,79}', key) or re.search(r'SECRET|PASSWORD|TOKEN|PRIVATE|CREDENTIAL|API_KEY|ACCESS_KEY|SESSION', key):
            fail('Nom de variable Vite publique interdit.')
        if not isinstance(value, str) or len(value) > 1024 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            fail('Valeur de variable Vite publique invalide.')
        declarations.append('ARG '+key+'\n')
        args.extend(['--opt', 'build-arg:'+key+'='+value])
    return ''.join(declarations), args

# The GitHub checkout may contain committed node_modules from Windows or another
# platform. Never copy those files on top of dependencies installed by npm ci:
# a non-executable node_modules/.bin/vite otherwise causes exit code 126.
VITE_CONTEXT_EXCLUDES = frozenset({
    '.git', '.github', '.ssh', '.aws', '.npmrc', SOURCE_MARKER, '.env', '.env.local', '.env.production',
    '.env.development', 'node_modules', 'dist', 'build', 'out', '.next', '.nuxt',
    '.turbo', '.vite', 'coverage', '.cache', '.venv', 'venv', '__pycache__', '.pytest_cache', 'vendor', 'target', '.gradle', '.mvn/wrapper', 'bootstrap/cache',
})


def prepare_vite_build_context(source_dir, destination):
    """Create a disposable BuildKit context without generated dependencies.

    The checked-out repository and user-defined Dockerfile are never modified.
    Preserve ordinary source files and symlinks; shutil.copytree must not follow
    symlinks outside the checkout. Only the trusted AUTOKOM Dockerfile is used.
    """
    def exclude_generated(_directory, names):
        return [name for name in names if name in VITE_CONTEXT_EXCLUDES or name.startswith('.env.') or name.endswith(('.pem','.key','.p12','.pfx'))]

    try:
        shutil.copytree(source_dir, destination, symlinks=True, ignore=exclude_generated)
        # Source .dockerignore cannot override AUTOKOM trusted build recipe.
        ignorefile = destination / '.dockerignore'
        if ignorefile.is_symlink() or ignorefile.is_file():
            ignorefile.unlink()
        elif ignorefile.exists():
            fail('Fichier .dockerignore du projet invalide.')
        ignorefile.write_text(
            '.git\nnode_modules\n.env\n.env.*\n*.pem\n*.key\n', encoding='utf-8')
    except (OSError, shutil.Error) as exc:
        fail('Préparation du contexte frontend impossible; vérifier les fichiers du dépôt GitHub.')


def protect_static_html_context(destination):
    # Public static assets only; never serve source or deployment secrets.
    allowed = {'.html','.htm','.css','.js','.mjs','.json','.txt','.xml',
        '.webmanifest','.svg','.png','.jpg','.jpeg','.gif','.webp','.avif',
        '.ico','.bmp','.woff','.woff2','.ttf','.otf','.eot','.wasm','.pdf',
        '.mp4','.mp3','.webm','.ogg','.wav','.map'}
    for item in sorted(destination.rglob('*'), key=lambda entry: len(entry.parts), reverse=True):
        if item.name == 'autokom-nginx.conf':
            continue
        if item.is_symlink():
            item.unlink()
        elif item.is_file() and (item.name.startswith('.') or item.suffix.lower() not in allowed):
            item.unlink()
        elif item.is_dir() and not any(item.iterdir()):
            item.rmdir()



def build(doc):
    path=source(doc)
    cfg=doc['build']
    profile = cfg.get('profile') or ('vite' if cfg['method'] == 'STATIC' else '')
    if not isinstance(profile, str):
        fail('Recette frontend invalide.')
    if cfg['method'] not in ('DOCKERFILE', 'STATIC', 'AUTO'):
        fail('Méthode de compilation non prise en charge.')
    if cfg['method'] == 'DOCKERFILE' and (cfg.get('start_command') or cfg.get('build_command')):
        fail('Commande personnalisée non autorisée pour le runner Dockerfile actuel.')
    if cfg['method'] == 'STATIC' and profile not in STATIC_PROFILES:
        fail('Recette statique non autorisée.')
    if cfg['method'] == 'AUTO' and profile != 'next_server' and profile not in BACKEND_PROFILES:
        fail('La recette serveur automatique demandée est inconnue.')
    # SQLite is permitted but warnings must survive UI bypass and appear in the Jenkins log.
    if cfg.get('runtime') in ('PYTHON', 'NODE', 'JAVA', 'PHP') or profile in BACKEND_PROFILES:
        try:
            warning = sqlite_storage_warning(path)
            if warning:
                print('AUTOKOM_LOG:AVERTISSEMENT_STOCKAGE_SQLITE ' + warning, flush=True)
        except (ValueError, OSError):
            print('AUTOKOM_LOG:Contrôle SQLite non concluant ; vérifier la persistance des données.', flush=True)
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
    elif profile == 'vite':
        if cfg.get('start_command'):
            fail('Commande de démarrage personnalisée non autorisée pour Vite.')
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
        try:
            from autokom_frontend_recipes import script_name
            build_script = script_name(cfg.get('build_command'))
            if build_script not in (parsed.get('scripts') or {}):
                fail('Script npm demandé absent de package.json.')
        except ValueError as exc:
            fail(str(exc))
        # Base images are provisioned by BuildKit and should be digest-pinned in production.
        # Only the trusted AUTOKOM recipe is executed, never an arbitrary user path.
        vite_arg_lines, vite_build_args = public_vite_build_args(doc)
        recipe = (
            'FROM node:22-alpine AS builder\n'
            'WORKDIR /app\n'
            + vite_arg_lines
            + 'COPY package.json package-lock.json ./\n'
            'RUN npm ci --ignore-scripts --no-audit --no-fund\n'
            'COPY . .\n'
            f'RUN npm run {build_script}\n'
            'RUN printf "%s\\n" "server {" "listen 8080;" "server_name _;" '
            '"root /usr/share/nginx/html;" "index index.html;" '
            '"location / { try_files \\$uri \\$uri/ /index.html; }" '
            '"add_header X-Content-Type-Options nosniff always;" "}" > /tmp/autokom-nginx.conf\n'
            'FROM nginxinc/nginx-unprivileged:1.29-alpine\n'
            'COPY --from=builder /tmp/autokom-nginx.conf /etc/nginx/conf.d/default.conf\n'
            'COPY --from=builder /app/dist/ /usr/share/nginx/html/\n'
            'EXPOSE 8080\n'
        )
    else:
        if profile in BACKEND_PROFILES:
            try:
                recipe, accompanying = backend_recipe(profile, path, cfg)
                frontend_args = []
            except (ValueError, OSError) as exc:
                fail(str(exc))
        else:
            if cfg.get('port') != 8080 or cfg.get('runtime') != ('NODE' if profile == 'next_server' else 'STATIC'):
                fail('Port et runtime frontend non conformes à la recette autorisée.')
            try:
                validate_profile_source(path, profile, cfg)
                recipe, frontend_args = frontend_recipe(profile, cfg, doc)
            except ValueError as exc:
                fail(str(exc))
    stage('build','RUNNING')
    buildkit_preflight()
    with tempfile.NamedTemporaryFile(dir=artifact_dir,prefix='.building-',suffix='.tar',delete=False) as tmp:
        temp=Path(tmp.name)
    try:
        if cfg['method'] in ('STATIC', 'AUTO'):
            with tempfile.TemporaryDirectory(prefix='autokom-frontend-build-') as trusted_dir:
                trusted_root = Path(trusted_dir)
                trusted_dockerfile = trusted_root/'recipe'
                trusted_context = trusted_root/'context'
                trusted_dockerfile.mkdir()
                prepare_vite_build_context(path, trusted_context)
                if profile == 'html':
                    protect_static_html_context(trusted_context)
                if profile in BACKEND_PROFILES:
                    # The generated Nginx/PHP config is always supplied by AUTOKOM.
                    for name, value in accompanying.items():
                        target = trusted_context / name
                        if target.is_symlink() or target.is_file(): target.unlink()
                        elif target.exists(): fail('Fichier réservé AUTOKOM dans le contexte.')
                        target.write_text(value, encoding='utf-8')
                if profile not in BACKEND_PROFILES and profile != 'vite':
                    # The only nginx configuration in the container comes from AUTOKOM.
                    conf = trusted_context / 'autokom-nginx.conf'
                    if conf.is_symlink() or conf.is_file():
                        conf.unlink()
                    elif conf.exists():
                        fail('Configuration Nginx du contexte invalide.')
                    conf.write_text(static_server_config(), encoding='utf-8')
                (trusted_dockerfile/'Dockerfile').write_text(recipe, encoding='utf-8')
                execute(['buildctl','build','--frontend','dockerfile.v0',
                         '--local','context='+str(trusted_context),
                         '--local','dockerfile='+str(trusted_dockerfile),
                         '--opt','filename=Dockerfile',
                         *(vite_build_args if profile == 'vite' else frontend_args),
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
    if len(sys.argv)!=2 or sys.argv[1] not in ('metadata','metadata-line','private-source','sast','sca','build'):
        fail('Phase de build inconnue.')
    doc=manifest()
    if sys.argv[1]=='metadata':
        print(json.dumps({'repo':doc['repository'],'sha':doc['commit_sha']}))
    elif sys.argv[1]=='metadata-line':
        # Strict single-line protocol; repo and SHA are validated in manifest().
        print(f"{doc['repository']} {doc['commit_sha']}")
    elif sys.argv[1] == 'private-source':private_source(doc)
    elif sys.argv[1] in ('sast','sca'):scan(doc,sys.argv[1])
    else:build(doc)

if __name__=='__main__':main()
