#!/usr/bin/env python3
"""AUTOKOM — CLI unique adaptatif (stdlib uniquement).

La simulation et l'inventaire ne modifient jamais la machine. En application,
chaque phase inspecte l'état courant puis ne lance que le module nécessaire.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import json
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from observability_config import load as load_settings, ConfigError
from tool_banner import section, message, colored_marker, Activity, phase_progress
from jenkins_network import bindings, private_listener, enforce, NetworkError

STAGES = ('foundation', 'services', 'plugins', 'observability')
LABELS = {
    'foundation': 'Fondation système',
    'services': 'Jenkins / services',
    'plugins': 'Plugins et sécurité Jenkins',
    'observability': 'Prometheus / Grafana',
}
REQUIRED_COMMANDS = ('nginx', 'certbot', 'docker', 'ansible-playbook',
                     'trivy', 'semgrep', 'nikto')


class CLIError(RuntimeError):
    pass


def command(args, *, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def works(args):
    p = command(args)
    return p is not None and p.returncode == 0


def http_code(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={'User-Agent':'AUTOKOM-Inventory/1'}), timeout=3) as result:
            return result.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, urllib.error.URLError, TimeoutError):
        return 0


def plugin_gaps(profile='devsecops'):
    # Existence sur disque : indication de provisioning, pas validation de la
    # compatibilité réelle de toutes les versions/extensions Jenkins.
    from jenkins_postinstall import plugin_ids, local_plugin_ids
    existing = local_plugin_ids()
    return tuple(p for p in plugin_ids(profile) if p.split(':', 1)[0] not in existing)


def prometheus_target_up(port, job):
    url = f'http://127.0.0.1:{port}/api/v1/targets'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=3) as response:
            obj = json.load(response)
        return any(t.get('health') == 'up' and
                   (t.get('labels', {}).get('job') == job or
                    t.get('discoveredLabels', {}).get('job') == job)
                   for t in obj.get('data', {}).get('activeTargets', []))
    except (OSError, ValueError, TypeError, AttributeError, urllib.error.URLError):
        return False


@dataclass(frozen=True)
class Snapshot:
    foundation_ok: bool
    jenkins_installed: bool
    jenkins_active: bool
    jenkins_private: bool
    jenkins_listening: bool
    jenkins_plugins_missing: tuple[str, ...]
    prometheus_active: bool
    prometheus_private: bool
    grafana_active: bool
    grafana_private: bool
    metrics_up: bool
    datasource_ok: bool
    ufw_active: bool
    domains_ready: bool = True


def domains_ok(cfg):
    """Verify only configured domains; never create ACME certificates in discovery."""
    if not (cfg.prometheus_domain or cfg.grafana_domain):
        return True
    try:
        from observability import existing_vhosts, vhost_target_ok, valid_cert
        return all(
            vhost_target_ok(existing_vhosts(domain), port) and valid_cert(domain)
            for domain, port in ((cfg.prometheus_domain, cfg.prometheus_port),
                                 (cfg.grafana_domain, cfg.grafana_port))
            if domain
        )
    except (ImportError, OSError, ValueError):
        return False


def snapshot(cfg):
    foundation_ok = all(shutil.which(item) for item in REQUIRED_COMMANDS) and works(['nginx', '-t'])
    pkg = command(['dpkg-query', '-W', '-f=${Status}', 'jenkins'])
    active_jenkins = works(['systemctl', 'is-active', '--quiet', 'jenkins'])
    installed = bool((pkg and pkg.returncode == 0 and pkg.stdout.strip() == 'install ok installed') or active_jenkins)
    j_listener = bindings(cfg.jenkins_port) if active_jenkins else []
    p_active = works(['systemctl', 'is-active', '--quiet', 'prometheus'])
    g_active = works(['systemctl', 'is-active', '--quiet', 'grafana-server'])
    p_listener = bindings(cfg.prometheus_port) if p_active else []
    g_listener = bindings(cfg.grafana_port) if g_active else []
    # Don't query authenticated endpoints before deployment. A 401 is healthy
    # for a private controller; the actual scraper check below is decisive.
    p_ready = p_active and http_code(cfg.prometheus_base + '/-/ready') == 200
    g_ready = g_active and http_code(cfg.grafana_base + '/api/health') == 200
    datasource_path = Path('/etc/grafana/provisioning/datasources/autokom-prometheus.yaml')
    try:
        datasource_ok = (datasource_path.is_file() and
                         cfg.prometheus_base in datasource_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError):
        datasource_ok = False
    ufw = command(['ufw', 'status'])
    return Snapshot(
        foundation_ok=bool(foundation_ok),
        jenkins_installed=installed,
        jenkins_active=active_jenkins,
        jenkins_private=private_listener(j_listener),
        jenkins_listening=bool(j_listener),
        jenkins_plugins_missing=plugin_gaps() if active_jenkins else (),
        prometheus_active=bool(p_ready and private_listener(p_listener)),
        prometheus_private=private_listener(p_listener),
        grafana_active=bool(g_ready and private_listener(g_listener)),
        grafana_private=private_listener(g_listener),
        metrics_up=prometheus_target_up(cfg.prometheus_port, cfg.jenkins_job) if p_ready else False,
        datasource_ok=datasource_ok,
        ufw_active=bool(ufw and 'Status: active' in ufw.stdout),
        domains_ready=domains_ok(cfg),
    )


def actions(state: Snapshot):
    """Plan purement fonctionnel : logique facile à tester, pas d'E/S."""
    return {
        'foundation': not state.foundation_ok,
        'services': not state.jenkins_installed or not state.jenkins_active or not state.jenkins_private,
        'plugins': not state.jenkins_installed or not state.jenkins_active or bool(state.jenkins_plugins_missing),
        'observability': (not state.prometheus_active or not state.grafana_active or
                          not state.metrics_up or not state.datasource_ok or not state.domains_ready),
    }


def selected(args):
    if args.only and args.start_at:
        raise CLIError('--only et --from ne peuvent pas être combinés')
    if args.only:
        return (args.only,)
    if args.start_at:
        return STAGES[STAGES.index(args.start_at):]
    return STAGES


def print_plan(state, plan, stages):
    section('Installation adaptative', '✓ conforme · + action · ! intervention nécessaire')
    if not state.ufw_active:
        message('  ! Pare-feu UFW inactif / indéterminé : vérifier AWS Security Groups et Docker.')
    for index, stage in enumerate(stages, 1):
        blocked = stage in ('plugins', 'observability') and not state.jenkins_active
        status = ('!  en attente de Jenkins' if blocked else
                  ('+  à configurer' if plan[stage] else '✓  déjà conforme, ignorer'))
        note = ''
        if stage == 'services' and state.jenkins_installed and state.jenkins_active and not state.jenkins_private:
            note = ' (écoute Jenkins à restreindre — validation nécessaire)'
        if stage == 'plugins' and plan[stage] and state.jenkins_active:
            note = f' ({len(state.jenkins_plugins_missing)} plugins manquants)'
        message(f'  {status} · {LABELS[stage]}{note}')
    message('  › Les bases de données existantes ne sont jamais reconfigurées sans choix explicite.')


def yesno(question):
    fd = None
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        os.write(fd, f'  ? {question} [o/N] : '.encode())
        raw = bytearray()
        while len(raw) < 128:
            chunk = os.read(fd, 1)
            if not chunk or chunk in (b'\n', b'\r'):
                break
            raw.extend(chunk)
        return raw.decode('utf-8', errors='replace').strip().casefold() in ('o', 'oui')
    except OSError as exc:
        raise CLIError('Confirmation requise : terminal interactif absent') from exc
    finally:
        if fd is not None:
            os.close(fd)


def call_module(path: str, *args):
    target = ROOT / path
    if not target.is_file():
        raise CLIError(f'Module absent : {target}')
    cmd = (['bash', str(target)] if target.suffix == '.sh' else [sys.executable, str(target)]) + list(args)
    p = subprocess.run(cmd, check=False)
    if p.returncode:
        raise CLIError(f'Phase interrompue ({p.returncode}) : {LABELS.get(Path(path).stem, path)}')


def apply_stage(stage, state, cfg):
    section(LABELS[stage], 'AUTOKOM · traitement uniquement des écarts')
    if stage == 'foundation':
        call_module('scripts/bootstrap/preflight.sh')
        call_module('scripts/bootstrap/ansible.sh')
        call_module('scripts/install/foundation.sh', '--apply')
    elif stage == 'services':
        if not state.jenkins_installed:
            # Le choix d'une BDD, du domaine et de l'accès Docker reste interactif
            # uniquement lors d'une première installation Jenkins.
            call_module('scripts/install/services.sh', '--apply')
        elif not state.jenkins_active:
            raise CLIError('Jenkins installé mais arrêté : diagnostic requis (journalctl -u jenkins). Aucun redémarrage automatique.')
        elif not state.jenkins_private:
            if not state.jenkins_listening:
                raise CLIError('Port Jenkins introuvable : revoir observability.ini et systemd')
            if not yesno(f'Jenkins exposé sur TCP/{cfg.jenkins_port}. Restreindre à 127.0.0.1 et redémarrer ?'):
                raise CLIError('Écoute Jenkins non corrigée : arrêt sécurisé avant observabilité')
            # The question must remain stable; animation begins only after
            # the answer, while systemd restarts and we verify the listener.
            message('  › Redémarrage Jenkins accepté ; vérification de l’écoute privée…')
            try:
                with Activity('Redémarrage Jenkins · écoute locale', delay=0.2):
                    outcome = enforce(cfg.jenkins_port, approved=True)
            except NetworkError as exc:
                raise CLIError(str(exc) + ' Vérifier : systemctl cat jenkins ; '
                               'systemctl show jenkins -p Environment ; ss -lntp.') from exc
            message(f'  ✓ Jenkins listener : {outcome}')
    elif stage == 'plugins':
        if not state.jenkins_active:
            raise CLIError('Configurer Jenkins avant ses plugins')
        # Noter le PID AVANT safe-restart : ne pas confondre ancien processus
        # avec Jenkins revenu après le chargement de ses nouveaux plugins.
        previous = command(['systemctl', 'show', '-p', 'MainPID', '--value', 'jenkins'])
        old_pid = previous.stdout.strip() if previous and previous.returncode == 0 else ''
        call_module('scripts/install/jenkins.sh', '--apply', '--interactive')
        deadline = time.monotonic() + 120
        with Activity('Vérification du redémarrage Jenkins'):
            while time.monotonic() < deadline:
                current = command(['systemctl', 'show', '-p', 'MainPID', '--value', 'jenkins'])
                new_pid = current.stdout.strip() if current and current.returncode == 0 else ''
                if (not old_pid or (new_pid and new_pid != old_pid)) and \
                        works(['systemctl', 'is-active', '--quiet', 'jenkins']) and \
                        private_listener(bindings(cfg.jenkins_port)):
                    return
                time.sleep(3)
        raise CLIError('safe-restart Jenkins non confirmé (jobs peut-être actifs) : relancer sudo autokom install après le redémarrage.')
    elif stage == 'observability':
        if not state.jenkins_active or not state.jenkins_private:
            raise CLIError('Jenkins doit être actif et privé avant Prometheus/Grafana')
        call_module('scripts/install/observability.sh', '--apply', '--interactive')


def perform(args, cfg):
    stages = selected(args)
    with Activity('Analyse de l’infrastructure'):
        state = snapshot(cfg)
    plan = actions(state)
    print_plan(state, plan, stages)
    if args.check:
        message('  ✓ Simulation en lecture seule : aucune installation lancée.')
        return 0
    if os.geteuid() != 0:
        raise CLIError('Utiliser sudo autokom install')
    for index, stage in enumerate(stages, 1):
        with Activity(f'Contrôle {LABELS[stage]}'):
            state = snapshot(cfg)
        should = actions(state)[stage]
        if not should:
            message(f'  - {LABELS[stage]} : déjà conforme, ignoré')
            phase_progress(index, len(stages), LABELS[stage], skipped=True)
            continue
        apply_stage(stage, state, cfg)
        # Avancement par phases TERMINEES (pas un pourcentage temporel).
        # La conformité réelle de tous les services est revérifiée ensuite.
        message(f'  ✓ {LABELS[stage]} : module exécuté, vérification finale à venir')
        phase_progress(index, len(stages), LABELS[stage])
    with Activity('Contrôle final des services'):
        state = snapshot(cfg)
    remaining = [LABELS[s] for s in stages if actions(state)[s]]
    if remaining:
        message('  ! À revoir : ' + ', '.join(remaining))
        message('  › sudo autokom check affiche les contrôles détaillés.')
        return 1
    message('  ✓ Toutes les phases sélectionnées sont conformes aux vérifications locales.')
    if not state.ufw_active:
        message('  ! Isolation WAN non prouvée : vérifier UFW + Security Groups AWS.')
    return 0


def brief(cfg):
    with Activity('Inventaire des services'):
        s = snapshot(cfg)
    plan = actions(s)
    section('État des services', 'Inventaire rapide · lecture seule')
    for stage in STAGES:
        if stage in ('plugins', 'observability') and not s.jenkins_active:
            status = '! en attente'
        else:
            status = '+ à traiter' if plan[stage] else '✓ conforme'
        message(f'  {status:<14} {LABELS[stage]}')
    if not s.ufw_active:
        message('  ! UFW inactif / inconnu, vérifier les règles du cloud')
    message('  › Source Grafana : provisioning vérifié, authentification API non vérifiée ici.')
    message('  › sudo autokom install  (reprend seulement les écarts)')
    return 0


def release_version():
    """Version produit stable, editable uniquement dans VERSION a la racine.

    Distincte de la revision Git : un `autokom update` ne change pas
    automatiquement 0.1, sauf si le mainteneur change VERSION.
    """
    try:
        value = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return 'indisponible'
    import re
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?', value):
        return 'invalide (fichier VERSION)'
    return value


def version_label():
    """Affiche version de produit puis revision Git independante."""
    version = f'AUTOKOM · version {release_version()}'
    info = command(['git', '-C', str(ROOT), 'describe', '--tags', '--always', '--dirty'], timeout=3)
    revision = info.stdout.strip() if info and info.returncode == 0 else ''
    return version + '\n' + (f'AUTOKOM · révision {revision}' if revision else
                              'AUTOKOM · révision indisponible (Git absent)')


def show_brand():
    """Render the official AUTOKOM logo only when the user asks for it.

    No system service is probed. Tool-specific banners are independently
    restricted to the first installation of the corresponding service.
    """
    script = ROOT / 'scripts/autokom-banner.sh'
    if script.is_file():
        try:
            result = subprocess.run(['bash', str(script)], check=False, timeout=5)
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Broken/missing banner must never make the CLI unavailable.
    print('\n  ◈ AUTOKOM · Automation by IKOM\n', flush=True)


class GitVersionAction(argparse.Action):
    """Resolve Git revision only when -v/--version is actually requested."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser._print_message(version_label() + '\n', sys.stdout)
        parser.exit(0)


def make_parser():
    p = argparse.ArgumentParser(prog='autokom', description='AUTOKOM — déployer, configurer et vérifier en une commande',
                                epilog='Exemples : sudo autokom install | autokom status | sudo autokom check')
    p.add_argument('-v', '--version', action=GitVersionAction,
                   help='Afficher la version/révision Git d’AUTOKOM')
    p.add_argument('-b', '--with-banner', action='store_true',
                   help='Afficher la grande bannière AUTOKOM (masquée par défaut)')
    sub = p.add_subparsers(dest='cmd')

    def subcommand(name, **kwargs):
        parser = sub.add_parser(name, **kwargs)
        # Works after the subcommand too: autokom install -v, autokom status -h.
        parser.add_argument('-v', '--version', action=GitVersionAction,
                            help='Afficher la version/révision Git d’AUTOKOM')
        # SUPPRESS avoids overriding a top-level -b with a subparser default.
        parser.add_argument('-b', '--with-banner', action='store_true',
                            default=argparse.SUPPRESS,
                            help='Afficher la grande bannière AUTOKOM')
        return parser

    i = subcommand('install', help='Détecte et traite automatiquement les composants manquants',
                   epilog='Exemples : sudo autokom install --check | sudo autokom install --from observability')
    i.add_argument('--check', action='store_true', help='Afficher le plan sans rien modifier')
    i.add_argument('--from', dest='start_at', choices=STAGES, help='Reprendre à partir de cette phase')
    i.add_argument('--only', choices=STAGES, help='Traiter une seule phase (mode expert)')
    subcommand('status', help='État synthétique des composants', epilog='Exemple : autokom status')
    subcommand('check', help='Audit complet (équivalent script verify/stack.py)', epilog='Exemple : sudo autokom check')
    subcommand('config', help='Voir les ports, URLs et domaines, sans secrets', epilog='Exemple : autokom config')
    c = subcommand('configure', help='Configuration interactive ciblée (expert)',
                   epilog='Exemple : sudo autokom configure observability')
    c.add_argument('service', choices=('services', 'plugins', 'observability'))
    subcommand('update', help='Mise à jour Git fast-forward uniquement (explicite)',
               epilog='Exemple : sudo autokom update')
    return p


def main(argv=None):
    parser = make_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    # argparse exits while processing -h/--help and -v/--version. Render the
    # opt-in logo first so argument ordering remains flexible, even with help.
    # Ignore any token after `--`, which marks the end of CLI options.
    option_tokens = arguments[:arguments.index('--')] if '--' in arguments else arguments
    if '-b' in option_tokens or '--with-banner' in option_tokens:
        show_brand()
    a = parser.parse_args(arguments)
    if not a.cmd:
        parser.print_help()
        return 0
    if a.cmd == 'update':
        if os.geteuid() != 0:
            raise CLIError('Utiliser sudo autokom update')
        if not (ROOT / '.git').exists():
            raise CLIError('AUTOKOM n’est pas une copie Git')
        p = command(['git', '-C', str(ROOT), 'status', '--porcelain'])
        if p is None or p.returncode or p.stdout.strip():
            raise CLIError('Dépôt modifié localement : mise à jour refusée')
        message('  + Mise à jour AUTOKOM explicite...')
        try:
            # Capture Git output so the temporary spinner does not fight with
            # progress/status printed by the child process on the same line.
            with Activity('Téléchargement et vérification Git', delay=0.2):
                result = subprocess.run(['git', '-C', str(ROOT), 'pull', '--ff-only'],
                                        capture_output=True, text=True, timeout=180,
                                        check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CLIError('Mise à jour Git interrompue ou trop longue') from exc
        if result.stdout:
            print(result.stdout, end='' if result.stdout.endswith('\n') else '\n')
        if result.stderr:
            print(result.stderr, end='' if result.stderr.endswith('\n') else '\n', file=sys.stderr)
        if result.returncode:
            raise CLIError(f'git pull a échoué (code {result.returncode})')
        return 0
    cfg = load_settings()
    if a.cmd == 'config':
        section('Paramètres', 'Aucune donnée sensible')
        for key, value in cfg.display().items():
            print(f'  {key:<34} {value}')
        return 0
    if a.cmd == 'check':
        return subprocess.call([sys.executable, str(ROOT / 'scripts/verify/stack.py'), '--scope', 'all'])
    if a.cmd == 'status':
        return brief(cfg)
    if a.cmd == 'configure':
        if os.geteuid() != 0:
            raise CLIError('Utiliser sudo autokom configure ...')
        names = {'services': 'services.sh', 'plugins': 'jenkins.sh',
                 'observability': 'observability.sh'}
        flags = ('--interactive',) if a.service != 'services' else ()
        call_module('scripts/install/' + names[a.service], '--apply', *flags)
        return 0
    return perform(a, cfg)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (CLIError, ConfigError, NetworkError, ValueError, OSError) as exc:
        message(f'  ✗ {exc}', stream=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        message('  ! Opération annulée', stream=sys.stderr)
        raise SystemExit(130)
