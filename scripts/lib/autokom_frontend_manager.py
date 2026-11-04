#!/usr/bin/env python3
"""Cycle de vie du frontend statique AUTOKOM (aucun service Node en production).

Mise en production explicite : build non privilégié, contrôle sécurité,
releases versionnées, bascule atomique du symlink, rollback contrôlé.
Ne touche ni aux vhosts backend, ni à BIND9, Cloudflare, Jenkins ou Docker.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / 'platform/frontend'
SECRETS = Path('/etc/autokom/secrets/frontend.env')
STATE = Path('/etc/autokom/frontend-deployment.json')
RELEASES = Path('/var/www/autokom/frontend/releases')
CURRENT = RELEASES.parent / 'current'
BUILDS = Path('/var/lib/autokom/frontend/builds')
NPM_CACHE = Path('/var/lib/autokom/frontend/npm-cache')
BUILDER = 'autokom-frontend-builder'
VHOST = Path('/etc/nginx/sites-available/autokom-frontend.conf')
MARKER = '# MANAGED BY AUTOKOM FRONTEND'
RULES = ROOT / 'pipelines/autokom-frontend/semgrep-rules.yml'
FQDN = re.compile(r'(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')
RELEASE_ID = re.compile(r'^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$')


class FrontendError(RuntimeError):
    pass


def domain(value):
    value = (value or '').strip().lower().rstrip('.')
    if not FQDN.fullmatch(value) or value.endswith(('.local', '.example', '.test', '.invalid')):
        raise FrontendError('Domaine public invalide.')
    return value


def run(args, **kw):
    # Pas de capture ni d'affichage des variables de production.
    try:
        subprocess.run(args, check=True, timeout=kw.pop('timeout', 1200), **kw)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise FrontendError(f'Échec de {Path(args[0]).name}. Voir les logs de la commande.') from exc


def require_root():
    if os.geteuid() != 0:
        raise FrontendError('Cette opération nécessite sudo.')


def read_state():
    if not STATE.is_file():
        raise FrontendError('Frontend non configuré. Lancer autokom app frontend setup --check.')
    return json.loads(STATE.read_text(encoding='utf-8'))


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.frontend-', dir=str(STATE.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.chmod(name, 0o600)
        os.replace(name, STATE)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def config_urls(args):
    from autokom_env_manager import read_env
    env = {} if args.domain and args.backend_domain else read_env(SECRETS)
    front = args.domain or env.get('FRONTEND_URL', '')
    back = args.backend_domain or env.get('BACKEND_URL', '')
    if front.startswith('https://'):
        front = urlsplit(front).hostname or ''
    if back.startswith('https://'):
        back = urlsplit(back).hostname or ''
    return domain(front), domain(back)


def ensure_source():
    required = ['package.json', 'package-lock.json', 'vite.config.js', 'index.html',
                'src/main.jsx', 'src/services/api.js']
    missing = [name for name in required if not (FRONTEND / name).is_file()]
    if missing:
        raise FrontendError('Code frontend incomplet : ' + ', '.join(missing))
    manifest = json.loads((FRONTEND / 'package.json').read_text())
    lock = json.loads((FRONTEND / 'package-lock.json').read_text())
    if manifest.get('name') != lock.get('name') or manifest.get('version') != lock.get('version'):
        raise FrontendError('package.json et package-lock.json divergent.')
    for key in ('dependencies', 'devDependencies'):
        if manifest.get(key, {}) != lock.get('packages', {}).get('', {}).get(key, {}):
            raise FrontendError('package-lock.json doit être régénéré et approuvé.')


def existing_vhost_conflicts(front):
    enabled = Path('/etc/nginx/sites-enabled')
    if not enabled.is_dir():
        raise FrontendError('Nginx sites-enabled manquant.')
    for file in enabled.iterdir():
        if file.name == VHOST.name:
            continue
        try:
            value = file.read_text(encoding='utf-8')
        except OSError:
            continue
        for found in re.findall(r'(?m)^\s*server_name\s+([^;]+);', value):
            if front in found.split():
                raise FrontendError(f'Domaine {front} déjà configuré : {file}. Refus d’écrasement.')


def nginx_config(front, back, port):
    # API Django directement via loopback, et non via une IP Cloudflare.
    return f'''{MARKER}
server {{
    listen 80;
    listen [::]:80;
    server_name {front};
    root /var/www/autokom/frontend/current;
    index index.html;
    server_tokens off;
    access_log /var/log/nginx/autokom-frontend.access.log;
    error_log /var/log/nginx/autokom-frontend.error.log warn;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "DENY" always;
    location = /__autokom/health {{ access_log off; add_header Cache-Control "no-store"; return 200 "autokom-frontend-ok\\n"; }}
    location ^~ /api/ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host {back};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;
        proxy_connect_timeout 10s;
        proxy_read_timeout 90s;
        proxy_redirect off;
        add_header Cache-Control "no-store" always;
    }}
    location ~* \\.(?:js|css|webp|png|jpg|jpeg|svg|ico|woff2?)$ {{
        try_files $uri =404;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }}
    location / {{
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache";
    }}
    location ~ /\\. {{ deny all; }}
}}
'''


def builder_prepare():
    require_root()
    if BUILDER not in [u.pw_name for u in pwd.getpwall()]:
        run(['useradd', '--system', '--home-dir', '/var/lib/autokom/frontend',
             '--shell', '/usr/sbin/nologin', '--user-group', BUILDER])
    for folder in (BUILDS, NPM_CACHE):
        folder.mkdir(parents=True, exist_ok=True)
        shutil.chown(folder, user=BUILDER, group=BUILDER)
        folder.chmod(0o700)


def backend_port():
    path = Path('/etc/autokom/app-runtime.json')
    if not path.is_file():
        return 8172
    try:
        value = int(json.loads(path.read_text())['port'])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return 8172
    if not 1024 <= value <= 65535:
        raise FrontendError('Port interne Django invalide.')
    return value


def setup(args):
    ensure_source()
    front, back = config_urls(args)
    if front == back:
        raise FrontendError('Les domaines frontend et backend doivent être distincts.')
    if args.tls_mode == 'certbot' and (not args.email or '@' not in args.email):
        raise FrontendError('Ajouter --email pour Let’s Encrypt.')
    if args.nginx_mode == 'existing' and args.tls_mode == 'certbot':
        raise FrontendError('Avec --nginx-mode existing, les certificats restent gérés par le vhost existant.')
    if args.nginx_mode == 'managed' and args.tls_mode == 'existing':
        if not VHOST.is_file() or ('ssl_certificate' not in VHOST.read_text(encoding='utf-8') and
                                   'listen 443 ssl' not in VHOST.read_text(encoding='utf-8')):
            raise FrontendError('TLS existing nécessite un vhost AUTOKOM HTTPS déjà actif.')
    previous = read_state() if STATE.is_file() else None
    if previous and previous.get('active') and (previous.get('domain') != front or
       previous.get('backend_domain') != back or previous.get('nginx_mode') != args.nginx_mode):
        raise FrontendError('Changement de domaine/mode du site actif : migration manuelle requise.')
    print(f'  Frontend : https://{front} · Backend : https://{back}')
    print(f'  Déploiement : {RELEASES.parent} · Nginx : {args.nginx_mode} · HTTPS : {args.tls_mode}')
    if args.nginx_mode == 'managed':
        existing_vhost_conflicts(front)
        if VHOST.exists() and MARKER not in VHOST.read_text(encoding='utf-8'):
            raise FrontendError('Vhost existant non géré par AUTOKOM : refus de modification.')
        if VHOST.exists() and 'listen 443 ssl' in VHOST.read_text(encoding='utf-8'):
            current = VHOST.read_text(encoding='utf-8')
            if (f'server_name {front};' not in current or
                '/var/www/autokom/frontend/current' not in current or
                'location ^~ /api/' not in current):
                raise FrontendError('Vhost TLS géré différent : corriger manuellement avant setup.')
    if not args.apply:
        print('  ✓ Prévisualisation : aucun fichier de production modifié.')
        return 0
    require_root()
    if not shutil.which('nginx') or not shutil.which('node') or not shutil.which('npm'):
        raise FrontendError('Installer Nginx, Node.js compatible Vite et npm avant setup.')
    if args.nginx_mode == 'managed' and args.tls_mode == 'certbot' and not shutil.which('certbot'):
        raise FrontendError('Certbot indisponible.')
    builder_prepare()
    RELEASES.mkdir(parents=True, exist_ok=True)
    RELEASES.chmod(0o755)
    from autokom_env_manager import read_env, write_env, backup, _validate
    values = read_env(SECRETS)
    values.update({'FRONTEND_URL': f'https://{front}', 'BACKEND_URL': f'https://{back}'})
    values.setdefault('VITE_AUTOKOM_API_BASE', '/api')
    values.setdefault('VITE_AUTOKOM_FRONTEND_ONLY', 'false')
    values.setdefault('VITE_AUTOKOM_API_ENABLED', 'true')
    values.setdefault('VITE_AUTOKOM_GITHUB_ENABLED', 'false')  # le sélecteur GitHub est simulé
    _validate('frontend', values)
    if SECRETS.exists():
        backup(SECRETS)
    write_env(SECRETS, values)
    if args.nginx_mode == 'managed':
        VHOST.parent.mkdir(parents=True, exist_ok=True)
        link = Path('/etc/nginx/sites-enabled') / VHOST.name
        original = VHOST.read_text(encoding='utf-8') if VHOST.exists() else None
        # Ne jamais supprimer la configuration HTTPS créée par Certbot.
        if original is None or ('listen 443 ssl' not in original and 'ssl_certificate' not in original):
            VHOST.write_text(nginx_config(front, back, backend_port()), encoding='utf-8')
            VHOST.chmod(0o644)
        created = not link.exists() and not link.is_symlink()
        if created:
            link.symlink_to(VHOST)
        try:
            run(['nginx', '-t'])
            run(['systemctl', 'reload', 'nginx'])
        except FrontendError:
            if original is None:
                VHOST.unlink(missing_ok=True)
            else:
                VHOST.write_text(original, encoding='utf-8')
            if created:
                link.unlink(missing_ok=True)
            raise
        if args.tls_mode == 'certbot':
            run(['certbot', '--nginx', '--non-interactive', '--agree-tos', '--redirect',
                 '--email', args.email, '-d', front], timeout=300)
    save_state({'domain': front, 'backend_domain': back, 'nginx_mode': args.nginx_mode,
                'tls_mode': args.tls_mode, 'releases': previous.get('releases', []) if previous else [],
                'active': previous.get('active') if previous else None})
    print('  ✓ Configuration frontend installée. Effectuer le deploy --check avant --apply.')
    return 0


def build_environment(source):
    # Seules des variables publiques explicitement allowlistées sont transmises à Vite.
    from autokom_env_manager import read_env
    values = read_env(SECRETS)
    allowed = {'VITE_AUTOKOM_FRONTEND_ONLY': {'true', 'false'},
               'VITE_AUTOKOM_API_ENABLED': {'true', 'false'},
               'VITE_AUTOKOM_GITHUB_ENABLED': {'true', 'false'},
               'VITE_AUTOKOM_API_BASE': {'/api'}}
    public = {}
    for key, accepted in allowed.items():
        value = values.get(key, '/api' if key.endswith('_BASE') else 'false')
        if value not in accepted:
            raise FrontendError('Configuration Vite invalide : '+key)
        public[key] = value
    # Pour l'instant GitHub est un composant de démonstration : ne pas l'annoncer connecté.
    if public['VITE_AUTOKOM_GITHUB_ENABLED'] != 'false':
        raise FrontendError('L’UI GitHub simulée doit rester désactivée jusqu’à la liaison OAuth réelle.')
    env = {'HOME': '/var/lib/autokom/frontend', 'PATH': '/usr/local/bin:/usr/bin:/bin',
           'NPM_CONFIG_CACHE': str(NPM_CACHE), 'CI': 'true', **public}
    return env


def copy_source(directory):
    directory.mkdir()
    for name in ('package.json', 'package-lock.json', 'vite.config.js', 'index.html'):
        shutil.copy2(FRONTEND / name, directory / name)
    for name in ('src', 'public', 'tests'):
        shutil.copytree(FRONTEND / name, directory / name)
    shutil.copy2(RULES, directory / '.semgrep-rules.yml')


def chown_recursive(folder):
    shutil.chown(folder, BUILDER, BUILDER)
    for directory, dirs, files in os.walk(folder):
        for item in dirs + files:
            shutil.chown(Path(directory) / item, BUILDER, BUILDER)


def builder_run(command, cwd, env):
    run(['runuser', '-u', BUILDER, '--', '/usr/bin/env', '-i',
         *(f'{key}={value}' for key, value in env.items()), *command], cwd=cwd)


def deploy(args):
    require_root()
    ensure_source()
    state = read_state()
    if not shutil.which('node') or not shutil.which('npm'):
        raise FrontendError('Node.js/npm absents.')
    for tool in ('semgrep', 'trivy'):
        if not shutil.which(tool):
            raise FrontendError(f'{tool} absent : bloqué (scans DevSecOps obligatoires).')
    if not RULES.is_file():
        raise FrontendError('Règles Semgrep frontend manquantes.')
    builder_prepare()
    env = build_environment(FRONTEND)
    if env['VITE_AUTOKOM_FRONTEND_ONLY'] != 'false' or env['VITE_AUTOKOM_API_ENABLED'] != 'true':
        print('  › Mode simulation/API désactivée configuré : l’interface demeure en démonstration.')
    # Une copie de travail isolée évite npm dans le checkout Git ou sur le vhost.
    with tempfile.TemporaryDirectory(prefix='build-', dir=BUILDS) as temp:
        stage = Path(temp) / 'source'
        copy_source(stage)
        chown_recursive(Path(temp))
        stage.chmod(0o700)
        print('  [1/6] Dépendances npm immuables, scripts tiers désactivés')
        builder_run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], stage, env)
        print('  [2/6] Tests frontend')
        builder_run(['npm', 'run', 'test'], stage, env)
        print('  [3/6] Audit dépendances (HIGH/CRITICAL bloquants)')
        builder_run(['npm', 'audit', '--audit-level=high'], stage, env)
        print('  [4/6] SAST Semgrep')
        builder_run(['semgrep', 'scan', '--config', '.semgrep-rules.yml', '--error', '--metrics', 'off', 'src'], stage, env)
        print('  [5/6] Analyse Trivy (vulnérabilités + secrets)')
        builder_run(['trivy', 'fs', '--scanners', 'vuln,secret', '--exit-code', '1',
                     '--severity', 'HIGH,CRITICAL', '--no-progress', '.'], stage, env)
        print('  [6/6] Compilation Vite, sans sourcemap publique')
        builder_run(['npm', 'run', 'build'], stage, env)
        dist = stage / 'dist'
        if not (dist / 'index.html').is_file() or not (dist / 'assets').is_dir():
            raise FrontendError('Build Vite incomplet : aucun artefact publiable.')
        if args.check:
            print('  ✓ Vérification terminée : aucun release publié, aucun Nginx modifié.')
            return 0
        if state['nginx_mode'] == 'managed':
            run(['nginx', '-t'])
        release_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+os.urandom(4).hex()
        release = RELEASES / release_id
        if release.exists():
            raise FrontendError('Identifiant de release déjà utilisé.')
        tmp = RELEASES / ('.'+release_id+'.incoming')
        shutil.copytree(dist, tmp)
        for base, dirs, files in os.walk(tmp):
            for filename in files:
                (Path(base) / filename).chmod(0o644)
            for dirname in dirs:
                (Path(base) / dirname).chmod(0o755)
        tmp.chmod(0o755)
        os.replace(tmp, release)
        try:
            activate(release, state)
        except Exception:
            # Une release orpheline peut être supprimée ultérieurement, pas de destruction auto.
            raise
        state['releases'] = list(dict.fromkeys([release_id, *state.get('releases', [])]))
        state['active'] = release_id
        save_state(state)
        print('  ✓ Frontend publié :', release_id)
    return 0


def activate(release, state):
    if not release.is_dir() or release.parent != RELEASES or not RELEASE_ID.fullmatch(release.name):
        raise FrontendError('Release non autorisée.')
    if not (release / 'index.html').is_file():
        raise FrontendError('Release invalide (index.html manquant).')
    old = CURRENT.resolve(strict=True) if CURRENT.is_symlink() and CURRENT.exists() else None
    if CURRENT.exists() and not CURRENT.is_symlink():
        raise FrontendError('Le chemin current existe sans être un lien symbolique : refus.')
    link = CURRENT.parent / '.current-next'
    if link.exists() or link.is_symlink():
        raise FrontendError('Bascule déjà en cours : lien temporaire présent.')
    link.symlink_to(release)
    os.replace(link, CURRENT)
    try:
        if state['nginx_mode'] == 'managed':
            run(['nginx', '-t'])
            run(['systemctl', 'reload', 'nginx'])
    except Exception:
        if old and old.is_dir():
            restore = CURRENT.parent / '.current-restore'
            restore.symlink_to(old)
            os.replace(restore, CURRENT)
        else:
            CURRENT.unlink(missing_ok=True)
        raise


def rollback(args):
    require_root()
    state = read_state()
    releases = state.get('releases', [])
    active = state.get('active')
    targets = [item for item in releases if item != active]
    requested = args.to or (targets[0] if targets else None)
    if not requested or not RELEASE_ID.fullmatch(requested) or requested not in releases:
        raise FrontendError('Aucune version précédente autorisée. Utiliser --to RELEASE_ID.')
    print(f'  Rollback : {active} -> {requested}')
    if not args.apply:
        print('  ✓ Prévisualisation, aucune modification effectuée.')
        return 0
    activate(RELEASES / requested, state)
    state['active'] = requested
    state['releases'] = [requested] + [r for r in releases if r != requested]
    save_state(state)
    print('  ✓ Version restaurée.')
    return 0


def status(args):
    state = read_state()
    active = state.get('active')
    print('  Domaine : https://' + state['domain'])
    print('  Backend : https://' + state['backend_domain'])
    print('  Release active : ' + (active or 'aucune'))
    valid = bool(active and CURRENT.is_symlink() and CURRENT.resolve() == RELEASES / active
                 and (RELEASES / active / 'index.html').is_file())
    print('  Artefact et symlink : ' + ('OK' if valid else 'ABSENT/INCOHÉRENT'))
    return 0 if valid else 1


def logs(args):
    require_root()
    path = Path('/var/log/nginx/autokom-frontend.access.log' if args.source == 'access'
                else '/var/log/nginx/autokom-frontend.error.log')
    if not path.is_file():
        raise FrontendError('Aucun journal Nginx frontend disponible pour ce type.')
    run(['tail', '-n', str(args.lines), str(path)], timeout=20)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='autokom app frontend')
    sub = parser.add_subparsers(dest='command', required=True)
    setup_p = sub.add_parser('setup', help='Préparer le frontend, le domaine, le reverse proxy et HTTPS')
    setup_p.add_argument('--domain', help='Domaine frontend, ex. auto.ik0m.tech')
    setup_p.add_argument('--backend-domain', help='Domaine Django, ex. backend.auto.ik0m.tech')
    setup_p.add_argument('--nginx-mode', choices=('managed', 'existing'), default='managed')
    setup_p.add_argument('--tls-mode', choices=('certbot', 'existing'), default='certbot')
    setup_p.add_argument('--email', help='Email ACME (nécessaire si --tls-mode certbot)')
    setup_p.add_argument('--check', action='store_true', help='Prévisualiser sans modifier')
    setup_p.add_argument('--apply', action='store_true', help='Effectuer explicitement le setup')
    deploy_p = sub.add_parser('deploy', help='Tests, scans, build Vite, publication atomique')
    deploy_p.add_argument('--check', action='store_true', help='Tester et compiler sans publier')
    deploy_p.add_argument('--apply', action='store_true', help='Publier explicitement')
    rollback_p = sub.add_parser('rollback', help='Revenir à une release précédente')
    rollback_p.add_argument('--to', help='Identifiant exact de release')
    rollback_p.add_argument('--check', action='store_true')
    rollback_p.add_argument('--apply', action='store_true')
    sub.add_parser('status', help='Domaine, release active et intégrité des liens')
    logs_p = sub.add_parser('logs', help='Journaux Nginx de ce frontend')
    logs_p.add_argument('--source', choices=('access', 'error'), default='error')
    logs_p.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.command == 'logs' and not 1 <= args.lines <= 1000:
        raise FrontendError('Nombre de lignes attendu entre 1 et 1000.')
    if args.command == 'setup':
        if args.apply and args.check:
            raise FrontendError('Choisir soit --check soit --apply.')
        return setup(args)
    if args.command == 'deploy':
        if args.apply == args.check:
            raise FrontendError('Préciser soit --check soit --apply.')
        return deploy(args)
    if args.command == 'rollback':
        if args.apply and args.check:
            raise FrontendError('Choisir soit --check soit --apply.')
        return rollback(args)
    if args.command == 'logs': return logs(args)
    return status(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except FrontendError as exc:
        print('AUTOKOM Frontend : ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
