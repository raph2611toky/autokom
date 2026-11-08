#!/usr/bin/env python3
"""AUTOKOM: convergent recovery for already-configured platform + private logs.

Single check/apply operation with no interactive prompts, no reset of user data,
no implicit switch of an existing Loki runtime, and no modification of UFW.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys


class RecoverError(RuntimeError):
    pass


def logging_target():
    """Identify the existing owner; failed native installation leaves a marker."""
    from logging_runtime import detected, NATIVE
    from logging_stack import STATE_DIR, installed_backend
    mode = detected()
    if mode == 'mixed':
        raise RecoverError('Loki/Alloy exécutés simultanément en Docker et systemd : arrêt sécurisé')
    if mode == 'none' and (NATIVE / '.autokom-installed').is_file():
        mode = 'systemd'
    elif mode == 'none':
        mode = 'docker'
    if mode == 'systemd':
        return mode, 'loki'
    if (STATE_DIR / 'config.alloy').is_file():
        return mode, installed_backend()
    return mode, 'loki'  # lightweight fresh install; never force OpenSearch


def frontend_targets():
    from autokom_frontend_manager import config_urls
    import argparse
    try:
        return config_urls(argparse.Namespace(domain=None, backend_domain=None))
    except Exception as exc:
        raise RecoverError('Domaine frontend/backend non défini dans les secrets existants ; '
                           'AUTOKOM refuse de deviner les domaines.') from exc


def ready_frontend(front):
    import autokom_frontend_manager as fm
    try:
        state = fm.read_state()
        active = state.get('active')
        release = fm.RELEASES / active if active else None
        if not (active and state.get('domain') == front and release.is_dir()
                and (release / 'index.html').is_file()
                and fm.CURRENT.is_symlink() and fm.CURRENT.resolve() == release):
            return False
        fm.verify_frontend_tls(front, attempts=1)
        return True
    except (fm.FrontendError, OSError, ValueError):
        return False


def run(*, apply=False):
    import autokom_app_manager as app
    import autokom_frontend_manager as frontmod
    from logging_runtime import detected, apply as apply_runtime
    from logging_stack import install_complete, apply_docker
    from cli_colors import pretty_print as print

    if apply and os.geteuid() != 0:
        raise RecoverError('Utiliser sudo autokom recover --apply')

    mode, backend = logging_target()
    front, back = frontend_targets()
    api_ok = app.verify_health()
    logs_ok = install_complete(backend, mode)
    ui_ok = ready_frontend(front)

    print('  AUTOKOM / REPRISE AUTOMATIQUE · '+('APPLICATION' if apply else 'SIMULATION'))
    print('  Backend Django : '+('✓ prêt' if api_ok else '! à réparer avec app deploy'))
    print(f"  Journaux : {'✓ prêts' if logs_ok else '+ remise en service'} ({backend}, {mode})")
    print('  Frontend : '+('✓ publié et HTTPS vérifié' if ui_ok else '+ TLS / build / publication à finaliser'))
    print('  Domaine : https://' + front + ' → https://' + back)
    print('  Politique : aucun effacement MySQL, aucun changement UFW, aucun basculement implicite Docker ↔ systemd')
    if not apply:
        print('  ✓ Simulation sans modification. Utiliser sudo autokom recover --apply.')
        return 0

    failures = []
    if not api_ok:
        try:
            # Reuse the existing production pipeline and its security gates.
            print('  › Backend : reprise du déploiement contrôlé')
            outcome = app.main(['deploy'])
            if outcome not in (None, 0) or not app.verify_health():
                raise RecoverError('Django ne répond pas après deploy')
            api_ok = True
            print('  ✓ Backend Django disponible')
        except Exception as exc:
            failures.append('Backend Django : '+str(exc))
            print('  ✗ '+failures[-1])

    if not logs_ok:
        try:
            print('  › Logs : remise en conformité '+mode+' ('+backend+')')
            if mode == 'systemd':
                apply_runtime('systemd', 'loki')
            else:
                apply_docker(backend)
            if not install_complete(backend, mode):
                raise RecoverError('Loki/Alloy ou datasource Grafana non validés après installation')
            logs_ok = True
            print('  ✓ Loki/Alloy et Grafana vérifiés')
        except Exception as exc:
            failures.append('Journaux : '+str(exc))
            print('  ✗ '+failures[-1])

    if not api_ok:
        failures.append('Frontend non publié : API Django indisponible')
    elif not ui_ok:
        try:
            # With pre-existing frontend.env, setup can recover from an ACME
            # failure without prompting or resetting the database.
            state_exists = frontmod.STATE.is_file()
            tls_ready = False
            if state_exists and frontmod.managed_certificate_installed(front):
                try:
                    frontmod.verify_frontend_tls(front, attempts=1)
                    tls_ready = True
                except frontmod.FrontendError:
                    pass
            if not tls_ready or not state_exists:
                print('  › Frontend : publication du challenge HTTP-01, TLS et Nginx')
                options = argparse.Namespace(domain=front, backend_domain=back,
                    nginx_mode='managed', tls_mode='certbot', email=None,
                    apply=True, check=False)
                frontmod.setup(options)
            if not ready_frontend(front):
                print('  › Frontend : scans DevSecOps, build React puis publication')
                outcome = frontmod.deploy(argparse.Namespace(apply=True, check=False))
                if outcome not in (None, 0):
                    raise RecoverError('Build frontend non validé')
            if not ready_frontend(front):
                raise RecoverError('Release ou TLS/SNI non validé après déploiement')
            ui_ok = True
            print('  ✓ Frontend HTTPS publié et vérifié')
        except Exception as exc:
            failures.append('Frontend : '+str(exc))
            print('  ✗ '+failures[-1])

    print('  AUTOKOM / BILAN : Django='+('OK' if api_ok else 'ERREUR')+
          ' · Journaux='+('OK' if logs_ok else 'ERREUR')+
          ' · Frontend='+('OK' if ui_ok else 'ERREUR'))
    if failures:
        raise RecoverError('; '.join(failures))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='autokom recover',
        description='Réparer les composants manquants, puis activer la plateforme de manière convergente')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Plan en lecture seule')
    group.add_argument('--apply', action='store_true', help='Appliquer sans questions répétées')
    args = parser.parse_args(argv)
    if not args.apply:
        return run(apply=False)
    lock_path = Path('/run/lock/autokom-recover.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoverError('Une reprise AUTOKOM est déjà en cours') from exc
        return run(apply=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RecoverError as exc:
        print('  ✗ '+str(exc), file=sys.stderr)
        raise SystemExit(1)
