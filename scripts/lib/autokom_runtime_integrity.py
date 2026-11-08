#!/usr/bin/env python3
"""Convergent, least-privilege repair of AUTOKOM's builder scanners and DNS watchers.

A runtime that can compile OCI images is not necessarily ready to ship them.
This module verifies the real unprivileged scanner invocations and the
systemd-managed domain watchers, then changes only the missing infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import tempfile

from cli_colors import pretty_print as print

ROOT = Path(__file__).resolve().parents[2]
RULES_SOURCE = ROOT / 'scripts/runtime/autokom-client-semgrep-rules.yml'
RULES_DIRECTORY = Path('/usr/local/share/autokom-security')
RULES_TARGET = RULES_DIRECTORY / 'client-semgrep-rules.yml'
UNIT_DIRECTORY = Path('/etc/systemd/system')
TEMPLATES = ROOT / 'platform/backend/deployment_templates'
DOMAIN_UNITS = ('autokom-domain-reconciler.service',
                'autokom-domain-reconciler.timer',
                'autokom-domain-reconciler.path')
WATCHERS = DOMAIN_UNITS[1:]
BUILDER = 'autokom-builder'
BUILDER_HOME = '/var/lib/autokom-jenkins-agent-v2'
BUILDER_PATH = '/opt/autokom/toolchains/buildkit/v0.34.2/bin:/usr/local/bin:/usr/bin:/bin'
BUILDER_CACHE = Path(BUILDER_HOME) / '.cache'
TRIVY_CACHE = BUILDER_CACHE / 'trivy'


class IntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Integrity:
    semgrep: bool
    trivy: bool
    rules: bool
    timer: bool
    path: bool

    @property
    def ready(self):
        return all((self.semgrep, self.trivy, self.rules, self.timer, self.path))


def _run(argv, *, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityError('Commande système indisponible : ' + str(argv[0])) from exc


def _unit_active(unit):
    try:
        result = _run(['systemctl', 'is-active', unit], timeout=8)
    except IntegrityError:
        return False
    return result.returncode == 0 and result.stdout.strip() == 'active'


@dataclass(frozen=True)
class ToolProbe:
    ok: bool
    code: int | None
    problem: str
    binary: str | None


def _tool_env(tool):
    variables = ['HOME=' + BUILDER_HOME, 'PATH=' + BUILDER_PATH,
                 'XDG_CACHE_HOME=' + str(BUILDER_CACHE)]
    if tool == 'trivy':
        variables.append('TRIVY_CACHE_DIR=' + str(TRIVY_CACHE))
    return variables


def _probe_builder_tool(tool):
    """Check the *real* Jenkins user + agent PATH, retain a safe failure class."""
    binary = shutil.which(tool, path=BUILDER_PATH)
    if not binary:
        return ToolProbe(False, None, 'binary-not-on-agent-path', None)
    try:
        pwd.getpwnam(BUILDER)
    except KeyError:
        return ToolProbe(False, None, 'builder-user-missing', binary)
    try:
        result = _run(['runuser', '-u', BUILDER, '--', '/usr/bin/env',
                       *_tool_env(tool), binary, '--version'], timeout=35)
    except IntegrityError:
        return ToolProbe(False, None, 'cannot-run-builder-probe', binary)
    if result.returncode == 0:
        if tool == 'trivy':
            # '--version' alone can succeed while the private scan cache is unusable.
            if not TRIVY_CACHE.is_dir() or TRIVY_CACHE.is_symlink():
                return ToolProbe(False, 0, 'cache-missing-or-symlink', binary)
            try:
                access = _run(['runuser', '-u', BUILDER, '--', 'test', '-w', str(TRIVY_CACHE)], timeout=8)
            except IntegrityError:
                return ToolProbe(False, None, 'cache-access-check-failed', binary)
            if access.returncode != 0:
                return ToolProbe(False, access.returncode, 'cache-not-writable', binary)
            for name in ('db/trivy.db', 'db/metadata.json', 'db/trivy.db.lock'):
                file = TRIVY_CACHE / name
                if file.exists():
                    read = _run(['runuser', '-u', BUILDER, '--', 'test', '-r', str(file)], timeout=8)
                    if read.returncode != 0:
                        return ToolProbe(False, read.returncode, 'cached-db-unreadable', binary)
        return ToolProbe(True, 0, 'ok', binary)
    # Do not forward arbitrary stderr (it may contain paths or credentials).
    message = (result.stderr + '\n' + result.stdout).lower()
    if 'permission denied' in message or 'operation not permitted' in message:
        reason = 'permission-denied'
    elif 'no such file' in message or 'not found' in message:
        reason = 'binary-or-library-missing'
    elif 'read-only file system' in message:
        reason = 'read-only-filesystem'
    elif 'timeout' in message:
        reason = 'timeout'
    elif 'error while loading shared libraries' in message:
        reason = 'library-loading-failed'
    else:
        reason = 'process-failed'
    return ToolProbe(False, result.returncode, reason, binary)


def _builder_binary(tool):
    return _probe_builder_tool(tool).ok


def trivy_diagnose():
    probe = _probe_builder_tool('trivy')
    print('  ◈ AUTOKOM · TRIVY · DIAGNOSTIC COMPTE BUILD')
    print('  › Identité testée : ' + BUILDER + ' (pas root)')
    print('  › PATH agent : ' + BUILDER_PATH)
    print('  › Binaire : ' + (probe.binary or 'introuvable'))
    print('  › Cache privé : ' + str(TRIVY_CACHE))
    print('  ' + ('✓' if probe.ok else '✗') + ' Trivy : ' + probe.problem +
          ('' if probe.code is None else ' · code=' + str(probe.code)))
    for path in (Path(BUILDER_HOME), BUILDER_CACHE, TRIVY_CACHE):
        if path.is_symlink():
            print('  ✗ ' + str(path) + ' : lien symbolique non autorisé')
            continue
        if not path.exists():
            print('  ! ' + str(path) + ' : absent')
            continue
        info = path.stat()
        print('  › ' + str(path) + ' : uid=' + str(info.st_uid) +
              ' mode=' + format(stat.S_IMODE(info.st_mode), '04o'))
    print('  › Réparer : sudo autokom runtime reconcile --apply')
    print('  › Journaux agent : sudo autokom runtime logs --lines 100')
    return probe


def _ensure_trivy_cache():
    """Repair only AUTOKOM's dedicated private cache, never broad host chmod."""
    try:
        account = pwd.getpwnam(BUILDER)
    except KeyError as exc:
        raise IntegrityError('Compte autokom-builder absent ; reprovisionner l’agent Jenkins.') from exc
    home = Path(BUILDER_HOME)
    if home.is_symlink() or not home.is_dir():
        raise IntegrityError('HOME isolé du builder absent ou symbolique : ' + str(home))
    hi = home.stat()
    if hi.st_uid != account.pw_uid or not (stat.S_IMODE(hi.st_mode) & 0o700) == 0o700:
        raise IntegrityError('HOME builder non contrôlé par autokom-builder ; exécuter runtime agent --apply.')
    if hi.st_mode & 0o022:
        raise IntegrityError('HOME builder accessible en écriture par un tiers ; réparation automatique refusée.')
    for directory in (BUILDER_CACHE, TRIVY_CACHE):
        if directory.is_symlink():
            raise IntegrityError('Cache Trivy symbolique : refus de modifier ' + str(directory))
        if not directory.exists():
            directory.mkdir(mode=0o700, parents=False)
            os.chown(directory, account.pw_uid, account.pw_gid)
        info=directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, account.pw_uid):
            raise IntegrityError('Cache Trivy appartenant à un compte inattendu : ' + str(directory))
        # Scope is an AUTOKOM-private directory under the isolated agent home.
        if info.st_uid == 0:
            os.chown(directory, account.pw_uid, account.pw_gid)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            os.chmod(directory, 0o700)
    for relative in ('db', 'db/trivy.db', 'db/metadata.json'):
        target = TRIVY_CACHE / relative
        if target.is_symlink():
            raise IntegrityError('Cache Trivy : lien symbolique refusé : ' + relative)
        if target.exists():
            info = target.stat()
            if (info.st_uid not in (0, account.pw_uid) or
                    (target.is_file() and info.st_nlink != 1)):
                raise IntegrityError('Cache Trivy : propriétaire ou liens inattendus : ' + relative)
            if info.st_uid == 0:
                os.chown(target, account.pw_uid, account.pw_gid)
            if target.is_dir():
                os.chmod(target, 0o700)
            elif relative != 'db':
                os.chmod(target, 0o600)
    # Test actual write permission; do not rely exclusively on chmod results.
    proof = ("import os,tempfile; p=os.environ['TRIVY_CACHE_DIR']; "
             "f,n=tempfile.mkstemp(dir=p,prefix='.autokom-write-'); "
             "os.close(f);os.unlink(n)")
    result = _run(['runuser','-u',BUILDER,'--','/usr/bin/env',
                   *_tool_env('trivy'),'/usr/bin/python3','-c',proof],timeout=12)
    if result.returncode:
        raise IntegrityError('Le builder ne peut pas écrire dans son cache Trivy privé ; '
                             'vérifier ACL, fichiers root et restrictions systemd.')
    return True

def _rules_readable():
    if RULES_TARGET.is_symlink() or not RULES_TARGET.is_file():
        return False
    try:
        result = _run(['runuser', '-u', BUILDER, '--', 'test', '-r', str(RULES_TARGET)],timeout=8)
        return result.returncode == 0
    except IntegrityError:
        return False


def inspect():
    return Integrity(semgrep=_builder_binary('semgrep'),
                     trivy=_builder_binary('trivy'),
                     rules=_rules_readable(),
                     timer=_unit_active(WATCHERS[0]), path=_unit_active(WATCHERS[1]))


def report(state):
    for label, ok in [('Semgrep · builder isolé',state.semgrep),
                      ('Trivy · builder isolé',state.trivy),
                      ('Règles SAST approuvées',state.rules),
                      ('Réconciliation DNS/HTTPS · timer',state.timer),
                      ('Réconciliation DNS/HTTPS · path',state.path)]:
        print('  ' + ('✓ ' if ok else '! ') + label + (' : conforme' if ok else ' : à réparer'))


def _trusted_source(path):
    if path.is_symlink() or not path.is_file():
        raise IntegrityError('Ressource AUTOKOM absente ou symbolique : ' + path.name)
    return path.read_bytes()


def _ensure_rules():
    data = _trusted_source(RULES_SOURCE)
    if len(data) < 100 or b'rules:' not in data:
        raise IntegrityError('Règles Semgrep du dépôt invalides : aucune installation réalisée.')
    if RULES_DIRECTORY.is_symlink():
        raise IntegrityError('Répertoire de sécurité symbolique non autorisé.')
    if not RULES_DIRECTORY.exists():
        RULES_DIRECTORY.mkdir(mode=0o755, parents=True, exist_ok=False)
    directory_info=RULES_DIRECTORY.stat()
    if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != 0:
        raise IntegrityError('Répertoire de règles non contrôlé par root.')
    if stat.S_IMODE(directory_info.st_mode) & 0o022:
        raise IntegrityError('Répertoire de règles modifiable par un autre compte.')
    if RULES_TARGET.is_symlink():
        raise IntegrityError('Fichier de règles symbolique non autorisé.')
    if RULES_TARGET.exists():
        info=RULES_TARGET.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise IntegrityError('Règles existantes non contrôlées par root.')
        if RULES_TARGET.read_bytes() == data:
            if stat.S_IMODE(info.st_mode) != 0o644:
                os.chmod(RULES_TARGET, 0o644)
            return False
        # Never erase a local customized policy without explicit review.
        raise IntegrityError('Règles Semgrep déjà présentes mais différentes : intervention requise.')
    fd, name = tempfile.mkstemp(prefix='.autokom-rules-',dir=RULES_DIRECTORY)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,RULES_TARGET)
        os.chmod(RULES_TARGET,0o644)
    finally:
        Path(name).unlink(missing_ok=True)
    return True


def _repair_trivy():
    """Repair the agent's private cache FIRST, never use blind APT reinstalls."""
    before = _probe_builder_tool('trivy')
    if before.ok:
        # A version test is not a cache write check: scan needs a writable cache.
        _ensure_trivy_cache()
        return
    if before.problem == 'binary-not-on-agent-path':
        raise IntegrityError('Trivy absent du PATH Jenkins : ' + BUILDER_PATH +
                             ' ; relancer le rôle security_cli, pas un chmod global.')
    print('  › Trivy : vérification et réparation du cache privé autokom-builder')
    _ensure_trivy_cache()
    after = _probe_builder_tool('trivy')
    if after.ok:
        print('  ✓ Trivy : exécution restaurée après correction du cache privé')
        return
    raise IntegrityError('Trivy reste indisponible pour ' + BUILDER +
                         ' (cause=' + after.problem +
                         ', exit=' + str(after.code) + '). '
                         'Exécuter sudo autokom runtime scanners --diagnose. '
                         'Aucune réinstallation APT à l’aveugle.')

def _ensure_units():
    missing=[]
    for unit in DOMAIN_UNITS:
        target=UNIT_DIRECTORY / unit
        if target.is_symlink():
            raise IntegrityError('Unité systemd symbolique non autorisée : '+unit)
        if target.exists():
            if not target.is_file():
                raise IntegrityError('Unité systemd invalide : '+unit)
            continue  # Do not overwrite custom unit files.
        content=_trusted_source(TEMPLATES/(unit+'.example'))
        if b'[Unit]' not in content or (unit.endswith('.service') and b'ExecStart=' not in content):
            raise IntegrityError('Modèle systemd incomplet : '+unit)
        missing.append((target,content))
    for target,content in missing:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o644)
        with os.fdopen(fd,'wb') as stream:
            stream.write(content)
        print('  + Unité systemd ajoutée : '+target.name)
    if missing and _run(['systemctl','daemon-reload'],timeout=30).returncode:
        raise IntegrityError('systemd daemon-reload a échoué.')
    for unit in WATCHERS:
        if not _unit_active(unit):
            print('  › Activation sécurisée : '+unit)
            if _run(['systemctl','enable','--now',unit],timeout=35).returncode:
                raise IntegrityError('Activation impossible : '+unit+' ; consulter systemctl status et journalctl.')
    if not all(_unit_active(unit) for unit in WATCHERS):
        raise IntegrityError('Timer ou path toujours inactif après activation.')


def reconcile(*, apply=False):
    before=inspect()
    print('  ◈ AUTOKOM / RUNTIME · SÉCURITÉ & DOMAINES · '+('APPLICATION' if apply else 'SIMULATION'))
    report(before)
    if not apply or before.ready:
        if before.ready:
            print('  ✓ Scanners DevSecOps et réconciliation DNS/HTTPS opérationnels')
        else:
            print('  › Correction : sudo autokom runtime reconcile --apply')
        return before
    if os.geteuid() != 0:
        raise IntegrityError('La réparation des scanners et services est réservée à root.')
    failures=[]
    if not before.rules:
        try:
            changed=_ensure_rules()
            print('  ✓ Règles Semgrep '+('installées' if changed else 'réutilisées')+' sans réinstallation du scanner')
        except IntegrityError as exc:
            failures.append('Règles : '+str(exc))
    if not before.semgrep:
        failures.append('Semgrep : exécution impossible par autokom-builder ; installer le CLI avec un environnement isolé accessible.')
    if not before.trivy:
        try:
            _repair_trivy()
            print('  ✓ Trivy vérifié sous le compte autokom-builder')
        except IntegrityError as exc:
            failures.append('Trivy : '+str(exc))
    if not (before.timer and before.path):
        try:
            _ensure_units()
            print('  ✓ Reconciler DNS/HTTPS : timer et path actifs')
        except IntegrityError as exc:
            failures.append('Domaines : '+str(exc))
    after=inspect()
    report(after)
    if failures or not after.ready:
        if not failures:
            failures.append('Un ou plusieurs contrôles de santé ont échoué après la réparation.')
        raise IntegrityError('; '.join(failures) +
                             ' ; aide : sudo autokom runtime scanners --diagnose')
    print('  ✓ Runtime complet : scanners et automatisation des domaines validés')
    return after
