"""AUTOKOM allowlisted frontend build recipes (never execute UI supplied shell)."""
import json
import re
from pathlib import Path
try:
    from .autokom_npm_install import install_step
except ImportError:  # Standalone trusted Jenkins runner.
    from autokom_npm_install import install_step

STATIC_PROFILES = frozenset({'vite', 'npm_static', 'next_export', 'html'})
ALL_PROFILES = STATIC_PROFILES | {'next_server'}
PUBLIC_PREFIXES = {
    'vite': ('VITE_',),
    'npm_static': ('REACT_APP_', 'VUE_APP_', 'VITE_'),
    'next_export': ('NEXT_PUBLIC_',),
    'next_server': ('NEXT_PUBLIC_',),
    'html': (),
}
_NPM_COMMAND = re.compile(r'^npm run ([a-zA-Z][a-zA-Z0-9:_-]{0,63})$')
_SAFE_RELATIVE = re.compile(r'^[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*$')
_SECRET = re.compile(r'SECRET|PASSWORD|TOKEN|PRIVATE|CREDENTIAL|API_KEY|ACCESS_KEY|SESSION|AUTH_KEY', re.I)
_KEY = re.compile(r'^[A-Z][A-Z0-9_]{0,99}$')


def safe_output(value, *, allow_empty=False):
    if value in ('', None) and allow_empty:
        return ''
    if not isinstance(value, str) or not _SAFE_RELATIVE.fullmatch(value) or any(x in ('..', '.') for x in value.split('/')):
        raise ValueError('Dossier de sortie non autorisé.')
    return value


def script_name(command):
    if command in (None, ''):
        return 'build'
    match = _NPM_COMMAND.fullmatch(command)
    if match is None:
        raise ValueError('Commande non autorisée : utiliser un script npm run NOM.')
    return match.group(1)


def public_build_args(doc, profile):
    variables = doc.get('public_build_variables', {})
    if not isinstance(variables, dict) or len(variables) > 40:
        raise ValueError('Variables frontend invalides.')
    prefixes = PUBLIC_PREFIXES[profile]
    declarations, args = [], []
    for key, value in sorted(variables.items()):
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not any(key.startswith(p) and len(key) > len(p) for p in prefixes) or _SECRET.search(key):
            raise ValueError('Nom de variable publique invalide pour ce framework.')
        if not isinstance(value, str) or len(value) > 1024 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError('Valeur de variable publique invalide.')
        declarations.append('ARG ' + key + '\n')
        args.extend(('--opt', 'build-arg:' + key + '=' + value))
    return ''.join(declarations), args


def validate_profile_source(path, profile, cfg):
    """Keep profile detection/validation inside the isolated builder too."""
    if profile not in ALL_PROFILES:
        raise ValueError('Recette frontend inconnue.')
    if profile == 'html':
        if not (path / 'index.html').is_file():
            raise ValueError('Site HTML : index.html absent du dossier source.')
        if cfg.get('build_command') or cfg.get('start_command'):
            raise ValueError('Un site HTML simple ne nécessite pas de commandes npm.')
        return None, ''
    manifest = path / 'package.json'
    lock = path / 'package-lock.json'
    if any(p.is_symlink() for p in (manifest, lock)) or not manifest.is_file() or not lock.is_file():
        raise ValueError('package.json et package-lock.json sont requis.')
    if manifest.stat().st_size > 65536:
        raise ValueError('package.json trop volumineux.')
    try:
        package = json.loads(manifest.read_text(encoding='utf-8'))
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise ValueError('package.json invalide.') from exc
    if not isinstance(package, dict):
        raise ValueError('package.json invalide.')
    scripts = package.get('scripts') or {}
    if not isinstance(scripts, dict):
        raise ValueError('Scripts npm invalides.')
    target = script_name(cfg.get('build_command'))
    if target not in scripts:
        raise ValueError('Script npm de compilation absent de package.json.')
    dependencies = {**(package.get('dependencies') or {}), **(package.get('devDependencies') or {})}
    if profile == 'vite' and 'vite' not in dependencies:
        raise ValueError('Recette Vite choisie sans dépendance vite.')
    if profile.startswith('next_') and 'next' not in dependencies:
        raise ValueError('Recette Next.js choisie sans dépendance next.')
    if cfg.get('start_command') not in ('', None, 'npm run start'):
        raise ValueError('Commande serveur personnalisée refusée (Next.js).')
    if profile == 'next_server' and not scripts.get('start'):
        raise ValueError('Next.js serveur nécessite un script npm start.')
    return package, target


def static_server_config():
    return ('server {\n'
            '  listen 8080;\n'
            '  server_name _;\n'
            '  root /usr/share/nginx/html;\n'
            '  index index.html;\n'
            '  location / { try_files $uri $uri.html $uri/ /index.html; }\n'
            '  location ~ /\\. { deny all; }\n'
            '  add_header X-Content-Type-Options nosniff always;\n'
            '}\n')


def recipe(profile, cfg, doc):
    """Generate a deterministic Dockerfile; only npm script NAMES are configurable."""
    output = cfg.get('output_directory') or {'vite': 'dist', 'npm_static': 'build', 'next_export': 'out', 'next_server': '', 'html': ''}[profile]
    if profile in STATIC_PROFILES and profile != 'html':
        output = safe_output(output)
    elif profile == 'html':
        output = safe_output(output, allow_empty=True)
    if profile == 'next_export' and output != 'out':
        raise ValueError('Next.js export utilise obligatoirement le dossier out.')
    if profile == 'next_server' and output not in ('', '.next'):
        raise ValueError('Next.js serveur utilise la sortie .next.')
    if profile == 'html':
        return ('FROM nginxinc/nginx-unprivileged:1.29-alpine AS assets\n'
                'USER root\n'
                'COPY ./ /site/\n'
                'RUN rm -f /site/autokom-nginx.conf\n'
                'FROM nginxinc/nginx-unprivileged:1.29-alpine\n'
             'USER root\n'
                'COPY --chmod=0644 autokom-nginx.conf /etc/nginx/conf.d/default.conf\n'
                'RUN chmod 0755 /etc/nginx /etc/nginx/conf.d && chmod 0644 /etc/nginx/nginx.conf /etc/nginx/mime.types\n'
                'COPY --chown=10001:10001 --from=assets /site/ /usr/share/nginx/html/\n'
                'USER 10001:10001\nRUN nginx -t\nEXPOSE 8080\n'), []
    declarations, args = public_build_args(doc, profile)
    build_name = script_name(cfg.get('build_command'))
    base = ('FROM node:22-alpine AS builder\n'
            'WORKDIR /app\n' + declarations +
            'COPY package.json package-lock.json ./\n' +
            install_step(doc.get('mode')) +
            'RUN cp package-lock.json /tmp/autokom-effective-package-lock.json\n'
            'COPY . .\n'
            'RUN cp /tmp/autokom-effective-package-lock.json package-lock.json\n'
            f'RUN npm run {build_name}\n')
    if profile == 'next_server':
        # For standalone exports, copy static/public into their expected relative location.
        # For normal Next builds the installed node_modules and .next are retained.
        base += ('RUN mkdir -p public && if [ -f .next/standalone/server.js ]; then '
                 'mkdir -p .next/standalone/.next; '
                 'cp -R .next/static .next/standalone/.next/static; '
                 'cp -R public .next/standalone/public; fi\n'
                 'FROM node:22-alpine\nWORKDIR /app\n'
                 'ENV NODE_ENV=production PORT=8080 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1 HOME=/tmp NPM_CONFIG_CACHE=/tmp/.npm\n'
                 'COPY --chown=10001:10001 --from=builder /app/package.json ./package.json\n'
                 'COPY --chown=10001:10001 --from=builder /app/node_modules ./node_modules\n'
                 'COPY --chown=10001:10001 --from=builder /app/.next ./.next\n'
                 'COPY --chown=10001:10001 --from=builder /app/public ./public\n'
                 'USER 10001:10001\nEXPOSE 8080\n'
                 'CMD ["sh","-c","if [ -f .next/standalone/server.js ]; then '
                 'node .next/standalone/server.js; else '
                 './node_modules/.bin/next start -p 8080 -H 0.0.0.0; fi"]\n')
        return base, args
    base += ('FROM nginxinc/nginx-unprivileged:1.29-alpine\n'
             'USER root\n'
             'COPY --chmod=0644 autokom-nginx.conf /etc/nginx/conf.d/default.conf\n'
                'RUN chmod 0755 /etc/nginx /etc/nginx/conf.d && chmod 0644 /etc/nginx/nginx.conf /etc/nginx/mime.types\n'
             f'COPY --chown=10001:10001 --from=builder /app/{output}/ /usr/share/nginx/html/\n'
             'USER 10001:10001\nRUN nginx -t\nEXPOSE 8080\n')
    return base, args
