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


def _builder_binary(tool):
    """Test with the actual Jenkins agent PATH and HOME; no root shell PATH leaks."""
    binary = shutil.which(tool, path=BUILDER_PATH)
    if not binary:
        return False
    # A world-accessible absolute executable is not sufficient: the builder
    # also needs access through all parent directories and shared libraries.
    try:
        pwd.getpwnam(BUILDER)
        result = _run(['runuser', '-u', BUILDER, '--', '/usr/bin/env',
                       'HOME=' + BUILDER_HOME, 'PATH=' + BUILDER_PATH,
                       'XDG_CACHE_HOME=' + BUILDER_HOME + '/.cache',
                       binary, '--version'], timeout=20)
        return result.returncode == 0
    except (KeyError, IntegrityError):
        return False


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
    """Reinstall ONLY the already-installed APT version of Trivy.

    No unpinned curl|sh, no chmod of arbitrary binaries and no new repository.
    If APT is unavailable the diagnostic remains explicit.
    """
    if _builder_binary('trivy'):
        return
    info=_run(['dpkg-query','-W','-f=${Version}','trivy'])
    if info.returncode != 0 or not re.fullmatch(r'[0-9A-Za-z.+:~\-]+',info.stdout.strip()):
        raise IntegrityError('Trivy absent d’APT : provisionner le dépôt officiel signé, puis relancer.')
    binary=shutil.which('trivy',path=BUILDER_PATH)
    if binary:
        owner=_run(['dpkg-query','-S',str(Path(binary).resolve())])
        if owner.returncode != 0 or not owner.stdout.lstrip().startswith('trivy:'):
            raise IntegrityError('Binaire Trivy hors paquet APT : refus de le remplacer automatiquement.')
    version=info.stdout.strip()
    print('  › Trivy : réparation du paquet APT déjà installé (version conservée)')
    res=_run(['apt-get','install','-y','--no-install-recommends','--reinstall','trivy='+version],timeout=300)
    if res.returncode:
        raise IntegrityError('Réinstallation Trivy refusée par APT ; vérifier son dépôt officiel et ses dépendances.')
    if not _builder_binary('trivy'):
        raise IntegrityError('Trivy reste inaccessible au builder après restauration APT ; consulter les permissions réelles.')


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
        raise IntegrityError('; '.join(failures))
    print('  ✓ Runtime complet : scanners et automatisation des domaines validés')
    return after
