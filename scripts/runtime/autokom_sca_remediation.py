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



class RemediationInputError(ValueError):
    """A fixed diagnostic code, never an untrusted npm field."""


def registry_spec(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        return False
    if value.startswith('npm:'):
        alias = value[4:].rsplit('@', 1)
        return len(alias) == 2 and bool(PACKAGE.fullmatch(alias[0])) and range_spec(alias[1])
    return range_spec(value)


def range_spec(value):
    return bool(re.fullmatch(r'[0-9A-Za-z.*~^<>=|+\- ]{1,1024}', value)) and not value.startswith('-')

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


def stable_version(value):
    match = re.fullmatch(r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})', value)
    return tuple(map(int, match.groups())) if match else None


def targeted_overrides(manifest, rows):
    """Use only Trivy fixes for installed transitive versions, not free-form commands."""
    protected = set()
    for key in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
        protected.update(manifest.get(key) or {})
    existing = manifest.get('overrides') or {}
    # Preserve every developer override, including nested rules, unmodified.
    if existing:
        return {}
    groups = {}
    for _, name, old, _, versions in rows:
        current = stable_version(old)
        groups.setdefault(name, []).append((current, versions))
    result = {}
    for name, entries in sorted(groups.items()):
        if name in protected or not re.fullmatch(r'(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name) or any(current is None for current, _ in entries):
            continue
        # Major zero is unstable. Only patch within the same minor there.
        family = lambda v: v[:2] if v[0] == 0 else v[:1]
        families = {family(current) for current, _ in entries}
        for current_family in sorted(families):
            compatible = [(old, versions) for old,versions in entries if family(old) == current_family]
            minima = []
            for old, versions in compatible:
                options = [v for raw in versions if (v := stable_version(raw)) and family(v) == current_family and v > old]
                if not options:
                    break
                minima.append(min(options))
            if len(minima) == len(compatible):
                # Match only the affected installed versions. Never force a
                # different major already present elsewhere in the tree.
                target = '.'.join(map(str, max(minima)))
                for old in sorted({v for v,_ in compatible}):
                    result[name+'@'+'.'.join(map(str,old))] = target
    return result if len(result) <= 64 else {}


def summary(before, after=None, lock=None, *, applied_overrides=None):
    count, secrets, rows = before
    remaining, final_secrets, _ = after or before
    # Only a globally clean rescan can prove that the original findings are absent.
    fixed = after is not None and remaining == final_secrets == 0
    if not fixed:
        remaining = count  # Failed candidates are discarded, not published.
    details = [dict(id=aid, package=pkg, installed=old, severity=severity, fixed_versions=list(versions))
               for aid,pkg,old,severity,versions in sorted(rows, key=lambda x: (x[3] != 'CRITICAL', x))[:32]]
    if fixed:
        entries = read_json(lock, MAX_LOCK)['packages']
        for detail in details:
            detail['applied_versions'] = sorted({info['version'] for location, info in entries.items()
                if location.rsplit('node_modules/',1)[-1] == detail['package']
                and isinstance(info.get('version'), str) and VERSION.fullmatch(info['version'])})[:6]
    manifest = lock.with_name('package.json') if fixed and applied_overrides else None
    return dict(overrides=applied_overrides if manifest else {},
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest else '',
                lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest() if fixed else '', count=count, secrets=max(secrets, final_secrets), remaining=remaining, remediated=fixed,
                findings=details)


def read_json(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Fichier d’analyse non valide.')
    return json.loads(path.read_text(encoding='utf-8'))


def validate_npm(path):
    manifest = read_json(path/'package.json', 65536)
    lock = read_json(path/'package-lock.json', MAX_LOCK)
    if manifest.get('workspaces') or lock.get('lockfileVersion') not in (2,3):
        raise RemediationInputError('NPM_LOCK_FORMAT_UNSUPPORTED')
    if not isinstance(lock.get('packages'), dict) or '' not in lock['packages']:
        raise RemediationInputError('NPM_LOCK_PACKAGES_MISSING')
    for data in [manifest, *lock['packages'].values()]:
        if data.get('link'):
            raise RemediationInputError('NPM_LOCAL_DEPENDENCY')
        for key in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
            if any(not PACKAGE.fullmatch(name) or not registry_spec(spec)
                   for name,spec in (data.get(key) or {}).items()):
                raise RemediationInputError('NPM_DEPENDENCY_SPEC_UNSUPPORTED')
        url = data.get('resolved')
        if url:
            parts = urlsplit(url)
            if parts.netloc != 'registry.npmjs.org' or parts.scheme != 'https' or parts.query or parts.fragment:
                raise RemediationInputError('NPM_REGISTRY_UNSUPPORTED')
    if any(manifest.get(key) for key in ('bundleDependencies','bundledDependencies')):
        raise RemediationInputError('NPM_BUNDLED_DEPENDENCY')
    # Overrides can redirect dependency resolution even with a clean lock.
    def overrides(values):
        if not isinstance(values, dict):
            return False
        return all(registry_spec(spec) or overrides(spec)
                   for spec in values.values())
    if not overrides(manifest.get('overrides') or {}):
        raise RemediationInputError('NPM_OVERRIDE_UNSUPPORTED')
    return manifest


def repair_lock(source, destination, execute, *, refresh=False, targeted=None):
    manifest = validate_npm(source)
    with tempfile.TemporaryDirectory(prefix='autokom-npm-security-') as directory:
        root = Path(directory)
        context = root/'input'
        context.mkdir()
        for name in ('package.json','package-lock.json'):
            (context/name).write_bytes((source/name).read_bytes())
        if targeted:
            manifest = {**manifest, 'overrides': targeted}
            (context/'package.json').write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8')
        # No customer scripts, npmrc, credentials or build args are passed.
        npm = 'npm install' if targeted else 'npm update' if refresh else 'npm audit fix'
        accept_findings = '' if refresh or targeted else ' || test "$?" -eq 1'
        # Only fixed npm error codes may leave the temporary build.
        diagnose = " || { rc=$?; grep -Eo 'npm (ERR!|error) code [A-Z][A-Z0-9_]+' /tmp/autokom-audit.log | head -3; exit \"$rc\"; }" if refresh or targeted else accept_findings
        (context/'Dockerfile').write_text(
            'FROM node:22-alpine AS npmfix\nWORKDIR /work\n'
            'COPY package.json package-lock.json ./\n'
            f'RUN set -eu; {npm} --package-lock-only --ignore-scripts --no-fund '
            '--registry=https://registry.npmjs.org/ --userconfig=/dev/null --globalconfig=/dev/null '
            f'--fetch-retries=1 --fetch-timeout=45000 > /tmp/autokom-audit.log 2>&1{diagnose}\n'
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
        if targeted:
            (destination/'package.json').write_bytes((context/'package.json').read_bytes())
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
