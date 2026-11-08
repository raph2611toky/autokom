"""Allowlisted backend production recipes for isolated AUTOKOM BuildKit.

Only the fixed profiles below are accepted; a repository never supplies a Dockerfile
or an arbitrary shell entry point to an AUTO build.  The applications themselves
remain untrusted code and must run inside the existing AUTOKOM container sandbox.
"""
import json
import re
from pathlib import Path
import sys
_ROOT_BACKEND = Path(__file__).resolve().parents[2] / "platform" / "backend"
if str(_ROOT_BACKEND) not in sys.path:
    sys.path.insert(0, str(_ROOT_BACKEND))

PYTHON = frozenset({'python_fastapi', 'python_flask', 'python_django', 'python_wsgi'})
NODE = frozenset({'node_express', 'node_nest'})
JAVA = frozenset({'java_spring_maven', 'java_spring_gradle'})
PHP = frozenset({'php_plain', 'php_laravel', 'php_symfony'})
BACKEND_PROFILES = PYTHON | NODE | JAVA | PHP
BACKEND_RUNTIME = {**dict.fromkeys(PYTHON, 'PYTHON'), **dict.fromkeys(NODE, 'NODE'),
                   **dict.fromkeys(JAVA, 'JAVA'), **dict.fromkeys(PHP, 'PHP')}
_SAFE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


def _require_file(path, name):
    item = path / name
    if item.is_symlink() or not item.is_file():
        raise ValueError(f'{name} requis dans le dossier source.')
    return item


def _read(path, name, limit=131072):
    item = _require_file(path, name)
    if item.stat().st_size > limit:
        raise ValueError(f'{name} est trop volumineux.')
    return item.read_text(encoding='utf-8')


def _python_entry(path, profile):
    if profile == 'python_django':
        _require_file(path, 'manage.py')
        candidates = [p for p in path.glob('*/wsgi.py') if p.is_file() and not p.is_symlink()
                      and _SAFE_NAME.fullmatch(p.parent.name)]
        if len(candidates) != 1:
            raise ValueError('Django : un seul module projet/wsgi.py est nécessaire.')
        return candidates[0].parent.name + '.wsgi:application'
    files = [name for name in ('main.py', 'app.py') if (path / name).is_file() and not (path / name).is_symlink()]
    if not files and profile == 'python_wsgi' and (path / 'wsgi.py').is_file():
        return 'wsgi:application'
    if not files:
        raise ValueError('Application Python : main.py ou app.py requis.')
    # No arbitrary module name supplied by a client is ever interpolated.
    return files[0][:-3] + ':app'


def validate_backend_source(path, profile, cfg):
    # SQLite is an advisory risk (ephemeral data), not a reason to reject a valid profile.
    if profile not in BACKEND_PROFILES:
        raise ValueError('Profil backend non autorisé.')
    if cfg.get('port') != 8080 or cfg.get('runtime') != BACKEND_RUNTIME[profile]:
        raise ValueError('Runtime ou port interne incompatible avec la recette approuvée.')
    if cfg.get('build_command') or cfg.get('start_command') or cfg.get('output_directory'):
        raise ValueError('Les commandes personnalisées ne sont pas autorisées pour les recettes backend automatiques.')
    if profile in PYTHON:
        text = _read(path, 'requirements.txt', 65536).lower()
        if profile == 'python_fastapi' and not re.search(r'(?m)^\s*fastapi(?:\s*[=<>~!\[]|\s*$)', text):
            raise ValueError('FastAPI doit figurer dans requirements.txt.')
        if profile == 'python_flask' and not re.search(r'(?m)^\s*flask(?:\s*[=<>~!\[]|\s*$)', text):
            raise ValueError('Flask doit figurer dans requirements.txt.')
        if profile == 'python_django' and not re.search(r'(?m)^\s*django(?:\s*[=<>~!\[]|\s*$)', text):
            raise ValueError('Django doit figurer dans requirements.txt.')
        return _python_entry(path, profile)
    if profile in NODE:
        package = json.loads(_read(path, 'package.json', 65536))
        _require_file(path, 'package-lock.json')
        if not isinstance(package, dict):
            raise ValueError('package.json invalide.')
        deps = {**(package.get('dependencies') or {}), **(package.get('devDependencies') or {})}
        scripts = package.get('scripts') or {}
        if profile == 'node_nest':
            if '@nestjs/core' not in deps or not isinstance(scripts.get('build'), str) or not isinstance(scripts.get('start:prod'), str):
                raise ValueError('NestJS nécessite @nestjs/core et les scripts build/start:prod.')
        elif 'express' not in deps or not isinstance(scripts.get('start'), str):
            raise ValueError('Express nécessite express et le script npm start.')
        return None
    if profile in JAVA:
        if profile == 'java_spring_maven':
            pom = _read(path, 'pom.xml', 196608)
            if 'spring-boot' not in pom:
                raise ValueError('Maven : projet Spring Boot attendu.')
        else:
            gradle = 'build.gradle.kts' if (path / 'build.gradle.kts').is_file() else 'build.gradle'
            content = _read(path, gradle, 196608)
            if 'org.springframework.boot' not in content:
                raise ValueError('Gradle : plugin Spring Boot attendu.')
        return None
    if profile in {'php_laravel', 'php_symfony'}:
        composer = json.loads(_read(path, 'composer.json', 65536))
        _require_file(path, 'composer.lock')
        required = (composer.get('require') or {}) if isinstance(composer, dict) else {}
        name = 'laravel/framework' if profile == 'php_laravel' else 'symfony/framework-bundle'
        if name not in required:
            raise ValueError(f'Composer : dépendance {name} attendue.')
        if profile == 'php_laravel':
            _require_file(path, 'artisan')
        _require_file(path, 'public/index.php')
    else:
        _require_file(path, 'index.php')
    return None


def _php_nginx(public_root):
    return ('worker_processes 1; pid /tmp/nginx.pid; events { worker_connections 1024; }\n'
            'http { include /etc/nginx/mime.types; default_type application/octet-stream;\n'
            'access_log /dev/stdout; error_log /dev/stderr warn;\n'
            'server { listen 8080; server_name _; root ' + public_root + '; index index.php index.html;\n'
            'location / { rewrite ^ /index.php last; }\n'
            'location ~* \\.(?:css|js|mjs|map|svg|png|jpg|jpeg|gif|webp|ico|woff|woff2|ttf|otf|pdf|txt)$ { try_files $uri =404; }\n'
            'location = /index.php { include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root/index.php; '
            'fastcgi_pass 127.0.0.1:9000; }\n'
            'location ~ \\.php$ { return 404; }\n'
            'location ~ /\\. { deny all; }\n'
            'add_header X-Content-Type-Options nosniff always; } }\n')


def backend_recipe(profile, path, cfg):
    """Return a trusted Dockerfile and its non-secret accompanying runtime config."""
    entry = validate_backend_source(path, profile, cfg)
    files = {}
    if profile in PYTHON:
        image = 'python:3.12-slim'
        install = ('FROM ' + image + '\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n'
                   'WORKDIR /app\nCOPY requirements.txt ./\n'
                   'RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0 uvicorn==0.37.0\n'
                   'COPY --chown=10001:10001 . .\nUSER 10001:10001\nEXPOSE 8080\n')
        if profile == 'python_fastapi':
            cmd = ['python', '-m', 'uvicorn', entry, '--host', '0.0.0.0', '--port', '8080']
        else:
            cmd = ['gunicorn', '--bind', '0.0.0.0:8080', '--workers', '2', entry]
        return install + 'CMD ' + json.dumps(cmd) + '\n', files
    if profile in NODE:
        base = ('FROM node:22-alpine AS builder\nWORKDIR /app\n'
                'COPY package.json package-lock.json ./\nRUN npm ci --ignore-scripts --no-audit --no-fund\n'
                'COPY . .\n')
        if profile == 'node_nest':
            base += 'RUN npm run build\n'
        base += 'RUN npm prune --omit=dev --ignore-scripts --no-audit --no-fund\n'
        base += ('FROM node:22-alpine\nWORKDIR /app\nENV NODE_ENV=production PORT=8080 HOST=0.0.0.0\n'
                 'COPY --chown=node:node --from=builder /app/ ./\nUSER node\nEXPOSE 8080\n')
        cmd = 'start:prod' if profile == 'node_nest' else 'start'
        return base + 'CMD ["npm", "run", "' + cmd + '"]\n', files
    if profile in JAVA:
        if profile == 'java_spring_maven':
            base = ('FROM maven:3.9-eclipse-temurin-21 AS builder\nWORKDIR /src\nCOPY . .\n'
                    'RUN mvn -B -DskipTests package && '
                    'find target -maxdepth 1 -name "*.jar" ! -name "original-*" '
                    '! -name "*-sources.jar" ! -name "*-javadoc.jar" -print -quit '
                    '| xargs -r -I{} cp "{}" /tmp/autokom-app.jar && test -s /tmp/autokom-app.jar\n')
        else:
            base = ('FROM gradle:8.14-jdk21 AS builder\nWORKDIR /src\nCOPY --chown=gradle:gradle . .\n'
                    'USER gradle\nRUN gradle bootJar --no-daemon && '
                    'find build/libs -maxdepth 1 -name "*.jar" ! -name "*-plain.jar" -print -quit '
                    '| xargs -r -I{} cp "{}" /tmp/autokom-app.jar && test -s /tmp/autokom-app.jar\n')
        return (base + 'FROM eclipse-temurin:21-jre-alpine\nWORKDIR /app\n'
                'ENV SERVER_PORT=8080\n'
                'COPY --chown=10001:10001 --from=builder /tmp/autokom-app.jar /app/app.jar\n'
                'USER 10001:10001\nEXPOSE 8080\nCMD ["java","-jar","/app/app.jar"]\n'), files
    php_root = '/app' if profile == 'php_plain' else '/app/public'
    files['autokom-php-nginx.conf'] = _php_nginx(php_root)
    files['autokom-php-fpm.conf'] = '[www]\nlisten = 127.0.0.1:9000\n'
    # Both Nginx and PHP-FPM run under the non-privileged www-data user.
    base = ('FROM php:8.3-fpm-alpine AS php-base\n'
            'RUN apk add --no-cache nginx icu-dev libzip-dev oniguruma-dev && '
            'docker-php-ext-install -j2 pdo_mysql mbstring intl zip bcmath opcache && '
            'mkdir -p /run/nginx /var/cache/nginx /tmp/php-fpm\n')
    if profile in {'php_laravel', 'php_symfony'}:
        base += ('FROM php-base AS deps\n'
                 'COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer\n'
                 'WORKDIR /app\nCOPY composer.json composer.lock ./\n'
                 'RUN COMPOSER_ALLOW_SUPERUSER=1 composer install '
                 '--no-dev --no-interaction --prefer-dist --no-progress '
                 '--no-scripts --no-plugins --optimize-autoloader\n')
    base += ('FROM php-base\nWORKDIR /app\nCOPY . .\n')
    if profile in {'php_laravel', 'php_symfony'}:
        base += 'COPY --from=deps /app/vendor /app/vendor\n'
    base += ('COPY autokom-php-nginx.conf /etc/nginx/nginx.conf\n'
             'COPY autokom-php-fpm.conf /usr/local/etc/php-fpm.d/zzzz-autokom.conf\n'
             'RUN chown -R www-data:www-data /app /run/nginx /var/cache/nginx /tmp/php-fpm\n'
             'USER www-data\nEXPOSE 8080\n'
             "CMD [\"sh\",\"-c\",\"php-fpm -D && exec nginx -g 'daemon off;'\"]\n")
    return base, files
