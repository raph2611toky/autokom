"""Trusted, isolated npm lock remediation. The caller always rescans the result."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

MAX_REPORT = 16 * 1024 * 1024
MAX_LOCK = 8 * 1024 * 1024
NPM_PROFILES = {'vite', 'npm_static', 'next_export', 'next_server', 'node_express', 'node_nest'}
PACKAGE = re.compile(r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]{1,100}\Z')
VERSION = re.compile(r'[0-9][0-9A-Za-z.+-]{0,60}\Z')
ADVISORY = re.compile(r'(?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|[A-Z][A-Z0-9-]{3,49})\Z')


def findings(document):
    rows, blockers, secrets = set(), 0, 0
    results = document.get('Results')
    if document.get('SchemaVersion') != 2 or (results is not None and not isinstance(results, list)):
        raise ValueError('Rapport Trivy invalide.')
    for result in results or []:
        for item in result.get('Vulnerabilities') or []:
            if item.get('Severity') in ('UNKNOWN','LOW','MEDIUM'):
                continue
            blockers += 1
            pkg, advisory, installed, severity = (item.get(k) for k in ('PkgName','VulnerabilityID','InstalledVersion','Severity'))
            if (not all(isinstance(x, str) for x in (pkg, advisory, installed, severity)) or not PACKAGE.fullmatch(pkg)
                    or not ADVISORY.fullmatch(advisory) or not VERSION.fullmatch(installed)
                    or severity not in ('HIGH', 'CRITICAL')):
                continue
            fixed = item.get('FixedVersion') or ''
            fixed = tuple(sorted({v.strip() for v in fixed[:400].split(',') if VERSION.fullmatch(v.strip())})[:6])
            rows.add((advisory, pkg, installed, severity, fixed))
        secrets += len(result.get('Secrets') or [])
    if blockers + secrets > 50000:
        raise ValueError('Rapport Trivy trop volumineux.')
    return blockers, secrets, rows


def summary(before, after=None, lock=None):
    count, secrets, rows = before
    remaining, final_secrets, _ = after or before
    # Only a globally clean rescan can prove that the original findings are absent.
    fixed = after is not None and remaining == final_secrets == 0
    details = [dict(id=aid, package=pkg, installed=old, severity=severity, fixed_versions=list(versions))
               for aid,pkg,old,severity,versions in sorted(rows, key=lambda x: (x[3] != 'CRITICAL', x))[:32]]
    if fixed:
        entries = read_json(lock, MAX_LOCK)['packages']
        for detail in details:
            detail['applied_versions'] = sorted({info['version'] for location, info in entries.items()
                if location.rsplit('node_modules/',1)[-1] == detail['package']
                and isinstance(info.get('version'), str) and VERSION.fullmatch(info['version'])})[:6]
    return dict(lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest() if fixed else '', count=count, secrets=max(secrets, final_secrets), remaining=remaining, remediated=fixed,
                findings=details)


def read_json(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Fichier d’analyse non valide.')
    return json.loads(path.read_text(encoding='utf-8'))


def validate_npm(path):
    manifest = read_json(path/'package.json', 65536)
    lock = read_json(path/'package-lock.json', MAX_LOCK)
    if manifest.get('workspaces') or lock.get('lockfileVersion') not in (2,3):
        raise ValueError('Format npm non pris en charge pour la correction automatique.')
    allowed_spec = re.compile(r'[0-9A-Za-z.*~^<>=|+\- xX]{1,120}\Z')
    if not isinstance(lock.get('packages'), dict) or '' not in lock['packages']:
        raise ValueError('Table des paquets npm manquante.')
    for data in [manifest, *lock['packages'].values()]:
        if data.get('link'):
            raise ValueError('Dépendance locale exclue de la correction automatique.')
        for key in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
            if any(not PACKAGE.fullmatch(name) or not isinstance(spec, str) or not allowed_spec.fullmatch(spec)
                   for name,spec in (data.get(key) or {}).items()):
                raise ValueError('Dépendance npm non standard.')
        url = data.get('resolved')
        if url:
            parts = urlsplit(url)
            if parts.netloc != 'registry.npmjs.org' or parts.scheme != 'https' or parts.query or parts.fragment:
                raise ValueError('Registre npm non pris en charge.')
    if any(manifest.get(key) for key in ('bundleDependencies','bundledDependencies')):
        raise ValueError('Dépendances embarquées exclues de la correction automatique.')
    # Overrides can redirect dependency resolution even with a clean lock.
    def overrides(values):
        if not isinstance(values, dict):
            return False
        return all((isinstance(spec, str) and allowed_spec.fullmatch(spec)) or overrides(spec)
                   for spec in values.values())
    if not overrides(manifest.get('overrides') or {}):
        raise ValueError('Override npm non pris en charge.')
    return manifest


def repair_lock(source, destination, execute, *, refresh=False):
    manifest = validate_npm(source)
    with tempfile.TemporaryDirectory(prefix='autokom-npm-security-') as directory:
        root = Path(directory)
        context = root/'input'
        context.mkdir()
        for name in ('package.json','package-lock.json'):
            (context/name).write_bytes((source/name).read_bytes())
        # No customer scripts, npmrc, credentials or build args are passed.
        npm = 'npm update' if refresh else 'npm audit fix'
        accept_findings = '' if refresh else ' || test "$?" -eq 1'
        (context/'Dockerfile').write_text(
            'FROM node:22-alpine AS npmfix\nWORKDIR /work\n'
            'COPY package.json package-lock.json ./\n'
            f'RUN set -eu; {npm} --package-lock-only --ignore-scripts --no-fund '
            '--registry=https://registry.npmjs.org/ --userconfig=/dev/null --globalconfig=/dev/null '
            f'--fetch-retries=1 --fetch-timeout=45000 > /tmp/autokom-audit.log 2>&1{accept_findings}\n'
            'FROM scratch\nCOPY --from=npmfix /work/package-lock.json /package-lock.json\n', encoding='utf-8')
        execute(['buildctl','build','--frontend','dockerfile.v0','--local','context='+str(context),
                 '--local','dockerfile='+str(context),'--no-cache',
                 '--output','type=local,dest='+str(root/'output')], timeout=240)
        fixed = root/'output'/'package-lock.json'
        read_json(fixed, MAX_LOCK)
        candidate = destination/'package-lock.json'
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError('Lockfile candidat non valide.')
        if fixed.read_bytes() == candidate.read_bytes():
            return False
        candidate.write_bytes(fixed.read_bytes())
        if validate_npm(destination) != manifest:
            raise ValueError('Manifeste npm modifié.')
        return True


def scan_tree(path, execute):
    with tempfile.TemporaryDirectory(prefix='autokom-trivy-') as directory:
        report = Path(directory)/'report.json'
        # Exit 0 means the scanner ran, not that the project is clean. Check both
        # findings and secrets from its structured result, never raw console text.
        execute(['trivy','fs','--scanners','vuln,secret','--list-all-pkgs=false',
                 '--include-dev-deps','--format','json','--output',str(report),
                 '--exit-code','0','--no-progress',str(path)], timeout=480)
        return findings(read_json(report, MAX_REPORT))
