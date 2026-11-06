#!/usr/bin/env python3
"""Guarded, offline integration of the already-committed AUTOKOM ingress module.

Run ONLY in the Kali Git checkout. No runtime services/Docker/Nginx/network
commands are executed. If tests fail, the original source is restored.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

EXPECTED = {
    'INSTALLER.py': 'ff44bbec6a9cbd23893237f70fdd428e35037b4692b0a4ebfbad190476dc0530',
    'scripts/lib/autokom_internal_ingress.py': '78d5bb6a3ad7be22d0f0b8156abbd9d2e374b24faf6f6aea8510ff254a3da54e',
    'tests/test_autokom_internal_ingress.py': 'ef6b7afa28caec29c3d15fd5af2d524d95cf031d69ccd9199554d24ad458aec2',
}
BROKER = 'scripts/lib/autokom_platform_broker.py'
ADMIN = 'scripts/lib/autokom_runtime_admin.py'
TEST = 'tests/test_autokom_ingress_activation_20261106.py'


def ensure(cond, text):
    if not cond:
        raise RuntimeError(text)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guard_existing_files(root):
    for rel, digest in EXPECTED.items():
        path = root / rel
        ensure(path.is_file() and not path.is_symlink(), f'Fichier requis manquant: {rel}')
        ensure(sha(path) == digest, f'Version inattendue du fichier {rel}: abandon sans modification')
    for rel in (BROKER, ADMIN):
        path = root / rel
        ensure(path.is_file() and not path.is_symlink(), f'Fichier source requis manquant: {rel}')
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--is-inside-work-tree'],
                            capture_output=True, text=True, timeout=8, check=False)
    ensure(result.returncode == 0 and result.stdout.strip() == 'true',
           'Le dossier donné doit être un dépôt Git AUTOKOM.')


def load_installer(root):
    spec = importlib.util.spec_from_file_location('autokom_original_installer', root / 'INSTALLER.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ensure(callable(getattr(module, 'patch', None)) and callable(getattr(module, 'patch_admin', None)),
           'INSTALLER.py ne comporte pas les fonctions attendues.')
    return module


def check_integration(broker, admin):
    ast.parse(broker)
    ast.parse(admin)
    signals = [
        ('import autokom_internal_ingress as ingress' in broker, 'import du module ingress'),
        ('ingress.preflight(run, PROXY, ENABLED)' in broker, 'préflight Docker/Nginx'),
        ('relay = ingress.create(' in broker, 'création du relais'),
        ('ingress.remove(' in broker, 'nettoyage/rollback'),
        ('_ingress_watchdog' in broker, 'réconciliation automatique'),
        ('--publish' not in broker, 'absence de publication de ports Docker'),
        ('private ingress doctor' in admin, 'diagnostic ingress dans runtime doctor'),
    ]
    missing = [title for ok, title in signals if not ok]
    ensure(not missing, 'Intégration incomplète: ' + ', '.join(missing))


def atomic_write(path, data: bytes):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.autokom-integration-', delete=False) as fd:
        fd.write(data)
        temp = Path(fd.name)
    try:
        os.chmod(temp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def run_tests(root):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root / 'scripts/lib')
    tests = [
        sys.executable, '-m', 'unittest', '-v',
        'tests.test_autokom_internal_ingress',
        'tests.test_autokom_ingress_activation_20261106',
    ]
    result = subprocess.run(tests, cwd=root, env=env, timeout=120, check=False)
    ensure(result.returncode == 0, 'Tests d’intégration échoués, fichiers restaurés.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('repository', type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = p.parse_args()
    root = a.repository.expanduser().resolve()
    guard_existing_files(root)
    installer = load_installer(root)
    broker = (root / BROKER).read_text(encoding='utf-8')
    admin = (root / ADMIN).read_text(encoding='utf-8')
    broker_integrated = 'import autokom_internal_ingress as ingress' in broker
    admin_integrated = 'private ingress doctor' in admin
    ensure(broker_integrated == admin_integrated,
           'Intégration partielle détectée : audit Git nécessaire, aucune modification.')
    already = broker_integrated and admin_integrated
    if already:
        new_broker, new_admin = broker, admin
    else:
        new_broker = installer.patch(broker)
        new_admin = installer.patch_admin(admin)
    check_integration(new_broker, new_admin)
    test_file = root / TEST
    fixture = (Path(__file__).parent / 'test_autokom_ingress_activation_20261106.py').read_bytes()
    ensure(not test_file.is_symlink(), 'Fichier test non conforme')
    if test_file.exists():
        ensure(test_file.read_bytes() == fixture, 'Test existant différent, abandon sans écrasement')
    if a.check:
        print('CHECK OK: module déjà présent, broker/doctor analysés, intégration préparée.')
        print('Statut:', 'déjà intégrée' if already else 'intégration encore manquante')
        print('Fichiers concernés:', BROKER, ADMIN, TEST)
        return 0
    if not already:
        status = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--', BROKER, ADMIN],
                                capture_output=True, text=True, timeout=8, check=True)
        ensure(not status.stdout.strip(), 'Broker/doctor ont des modifications locales : préserver ces travaux avant application.')
    original = {
        root / BROKER: (root / BROKER).read_bytes(),
        root / ADMIN: (root / ADMIN).read_bytes(),
    }
    changed = []
    test_created = not test_file.exists()
    try:
        if not already:
            for rel, content in [(BROKER, new_broker.encode()), (ADMIN, new_admin.encode())]:
                target = root / rel
                atomic_write(target, content)
                changed.append(target)
        if test_created:
            atomic_write(test_file, fixture)
        run_tests(root)
    except Exception:
        for path in reversed(changed):
            atomic_write(path, original[path])
        if test_created:
            test_file.unlink(missing_ok=True)
        raise
    print('APPLY OK: broker, runtime doctor et tests cohérents ; aucune commande système exécutée.')
    print('Vérifier maintenant la suite complète des tests puis git diff avant commit.')


if __name__ == '__main__':
    try:
        raise SystemExit(main() or 0)
    except Exception as exc:
        print('ABORT (sans modification persistante):', exc, file=sys.stderr)
        raise SystemExit(2)
