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
OPTIONAL_STAGES = ('logs',)
# Alias publics : uniquement des étapes réellement prises en charge.
INSTALL_ALIASES = {'core': ('foundation',), 'pipeline': ('services','plugins'),
                   'cicd': ('services','plugins'), 'metrics': ('observability',),
                   'monitoring': ('observability',), 'logging': ('logs',)}
LABELS = {
    'foundation': 'Fondation système',
    'services': 'Jenkins / services',
    'plugins': 'Plugins et sécurité Jenkins',
    'observability': 'Prometheus / Grafana',
    'logs': 'Loki / Alloy + OpenSearch / Data Prepper / Dashboards',
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
    node_ready: bool = True
    alertmanager_ready: bool = True
    dashboards_ready: bool = True
    logs_ready: bool = True


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
    from observability_dashboards import needs_update
    dashboards_ready = not needs_update(cfg.datasource_uid,cfg.jenkins_job)
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
        dashboards_ready=dashboards_ready,
        ufw_active=bool(ufw and 'Status: active' in ufw.stdout),
        domains_ready=domains_ok(cfg),
        node_ready=(works(['systemctl','is-active','--quiet','prometheus-node-exporter']) and
                    private_listener(bindings(9100)) and http_code('http://127.0.0.1:9100/metrics') == 200),
        alertmanager_ready=(works(['systemctl','is-active','--quiet','prometheus-alertmanager']) and
                            private_listener(bindings(9093)) and http_code('http://127.0.0.1:9093/-/ready') == 200),
        logs_ready=__import__('logging_stack').ready('both'),
    )


def actions(state: Snapshot):
    """Plan purement fonctionnel : logique facile à tester, pas d'E/S."""
    return {
        'foundation': not state.foundation_ok,
        'services': not state.jenkins_installed or not state.jenkins_active or not state.jenkins_private,
        'plugins': not state.jenkins_installed or not state.jenkins_active or bool(state.jenkins_plugins_missing),
        'observability': (not state.prometheus_active or not state.grafana_active or
                          not state.metrics_up or not state.datasource_ok or not state.domains_ready or
                          not state.node_ready or not state.alertmanager_ready or not state.dashboards_ready),
        'logs': not getattr(state, 'logs_ready', True),
    }


def selected(args):
    module = getattr(args, 'module', None)
    if sum(bool(x) for x in (module, args.only, args.start_at)) > 1:
        raise CLIError('Choisir une seule cible : MODULE, --only ou --from')
    if module or args.only:
        target = module or args.only
        return INSTALL_ALIASES.get(target, (target,))
    if args.start_at:
        if args.start_at == 'logs':
            return ('logs',)
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


def choose_logs_backend(requested):
    """Select a safe logging stack using AUTOKOM's existing keyboard UI.

    No second ad-hoc input loop: menu.pick owns raw-mode, arrow navigation,
    immediate 1–9 selection, Enter, cancellation and terminal restoration.
    An unattended installation needs an explicitly selected feasible backend.
    """
    if requested != 'both':
        return requested
    from logging_stack import platform_resources
    total, _ = platform_resources()
    gib = total / (1024 ** 3)
    if not total or gib >= 12:
        return requested

    # Match preflight's hard RAM thresholds, not the *recommended* targets.
    options = [('loki', 'Loki + Alloy (léger, recommandé ici)')]
    if gib >= 5:
        options.append(('opensearch', 'OpenSearch + Data Prepper + Dashboards (plus gourmand)'))
    if gib >= 8:
        options.append(('both', 'Installer les deux (12 Gio recommandés)'))
    message(f'  ! Hôte : {gib:.1f} Gio de RAM ; installation conjointe recommandée à partir de 12 Gio.')
    message('  › AUTOKOM ne force pas les deux moteurs et ne modifie encore aucun service.')
    if gib < 1:
        raise CLIError('RAM insuffisante même pour Loki (minimum 1 Gio) : aucune installation.')

    from menu import pick
    try:
        index = pick('AUTOKOM · Choisir la stack de journaux',
                     [label for _, label in options], default=1,
                     cancel_label='Annuler sans modifier l’infrastructure')
    except RuntimeError as exc:
        # Do not fall back to implicit choice or block the CI / SSH without TTY.
        raise CLIError('RAM limitée et terminal non interactif : choisir explicitement '
                       '`--backend loki` ou `--backend opensearch` (si RAM >= 5 Gio).') from exc
    if index == 0:
        raise KeyboardInterrupt
    chosen, label = options[index - 1]
    message(f'  ✓ Choix explicite : {label}')
    return chosen


def choose_logs_runtime(requested):
    """Reuse the common AUTOKOM selector (arrows, number keys, Enter, q/0)."""
    if requested in ('docker','systemd'):return requested
    from logging_runtime import detected
    current=detected()
    if current=='mixed':raise CLIError('Loki/Alloy actifs en Docker et systemd : corriger ce conflit avant migration')
    from menu import pick
    choices=[('docker','Docker Compose — conteneurs gérés par AUTOKOM'),
             ('systemd','Services Linux natifs — commandes loki/alloy + systemctl')]
    default=2 if current=='systemd' else 1
    try:
        index=pick('AUTOKOM · Choisir le mode Loki + Alloy',
                   [label + (' [actuel]' if mode==current else '') for mode,label in choices],
                   default=default,cancel_label='Annuler sans modifier l’infrastructure')
    except RuntimeError as exc:
        raise CLIError('Terminal interactif requis, ou préciser --runtime docker|systemd') from exc
    if index==0:raise KeyboardInterrupt
    result=choices[index-1][0]
    message('  ✓ Mode explicitement choisi : '+result)
    return result


def call_module(path: str, *args):
    target = ROOT / path
    if not target.is_file():
        raise CLIError(f'Module absent : {target}')
    cmd = (['bash', str(target)] if target.suffix == '.sh' else [sys.executable, str(target)]) + list(args)
    p = subprocess.run(cmd, check=False)
    if p.returncode:
        raise CLIError(f'Phase interrompue ({p.returncode}) : {LABELS.get(Path(path).stem, path)}')


def apply_stage(stage, state, cfg, *, logs_backend='both', logs_runtime='docker'):
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
    elif stage == 'logs':
        call_module('scripts/install/logs.sh', '--apply', '--backend', logs_backend, '--runtime', logs_runtime)


def perform(args, cfg):
    stages = selected(args)
    if (args.backend or args.runtime) and 'logs' not in stages:
        raise CLIError('--backend et --runtime sont réservés à la phase logs')
    with Activity('Analyse de l’infrastructure'):
        state = snapshot(cfg)
    plan = actions(state)
    # Contrôle ciblé du backend plutôt que de considérer 'both' toujours requis.
    if 'logs' in stages:
        from logging_runtime import detected, runtime_ready
        mode = args.runtime or detected()
        from logging_stack import install_complete
        plan['logs'] = not install_complete(args.backend or ('loki' if mode=='systemd' else 'both'),mode)
    print_plan(state, plan, stages)
    if args.check:
        if 'logs' in stages:
            from logging_stack import resource_warnings, platform_resources
            for warning in resource_warnings(args.backend or 'both'):
                message('  ! '+warning)
            ram, _ = platform_resources()
            if (args.backend or 'both') == 'both' and ram and ram < 12 * 1024**3:
                message('  › RAM limitée : installation interactive proposera Loki ou OpenSearch ; '
                        'ou choisir --backend loki / --backend opensearch.')
        message('  ✓ Simulation en lecture seule : aucune installation lancée.')
        return 0
    if os.geteuid() != 0:
        raise CLIError('Utiliser sudo autokom install')
    logs_backend = args.backend or 'both'
    logs_runtime = 'docker'
    if 'logs' in stages:
        logs_runtime = choose_logs_runtime(args.runtime)
        if logs_runtime=='systemd' and args.backend is None:
            logs_backend = 'loki'
        else:
            logs_backend = choose_logs_backend(logs_backend)
        if logs_runtime=='systemd' and logs_backend!='loki':
            raise CLIError('Mode systemd Loki+Alloy seulement ; OpenSearch/dual reste Docker')
    for index, stage in enumerate(stages, 1):
        with Activity(f'Contrôle {LABELS[stage]}'):
            state = snapshot(cfg)
        should = (not __import__('logging_stack').install_complete(logs_backend,logs_runtime)
                  if stage == 'logs' else actions(state)[stage])
        if not should:
            message(f'  - {LABELS[stage]} : déjà conforme, ignoré')
            phase_progress(index, len(stages), LABELS[stage], skipped=True)
            continue
        if stage == 'logs' and __import__('logging_runtime').runtime_ready(logs_backend,logs_runtime):
            # Existing Loki/Alloy healthy: repair only missing Grafana integration.
            # Never recreate containers, reinstall native packages or restart Loki.
            from logging_stack import provision_grafana, sync_grafana_after_install
            from logging_monitoring import apply as apply_monitoring
            message('  › Loki/Alloy sains : réconciliation Grafana uniquement (sans toucher aux moteurs)')
            changed=provision_grafana()
            apply_monitoring()
            sync_grafana_after_install(changed=changed)
        else:
            apply_stage(stage, state, cfg, logs_backend=logs_backend,logs_runtime=logs_runtime)
        # Avancement par phases TERMINEES (pas un pourcentage temporel).
        # La conformité réelle de tous les services est revérifiée ensuite.
        message(f'  ✓ {LABELS[stage]} : module exécuté, vérification finale à venir')
        phase_progress(index, len(stages), LABELS[stage])
    with Activity('Contrôle final des services'):
        state = snapshot(cfg)
    remaining = [LABELS[s] for s in stages if (not __import__('logging_stack').install_complete(logs_backend,logs_runtime)
                 if s == 'logs' else actions(state)[s])]
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
    from logging_stack import backend_state
    from logging_runtime import detected, runtime_ready
    runtime=detected()
    message(f'  {"✓ opérationnel" if runtime_ready("loki",runtime) else "+ à configurer"}  Logs Loki/Alloy ({runtime}) · préférence {backend_state()}')
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


def color_git_diffstat(value: str, *, enabled: bool) -> str:
    """Colorize Git's diffstat graphs, which Git does not always color itself.

    Does not alter file names, source content or plain-text logs; only the
    +/- graph and final insertions/deletions counters when a TTY is present.
    """
    if not enabled:
        return value
    import re
    value = re.sub(r'\x1b\[[0-9;]*m', '', value)
    green = '\033[32m'
    red = '\033[31m'
    reset = '\033[0m'
    lines=[]
    for raw in value.splitlines(keepends=True):
        ending = '\n' if raw.endswith('\n') else ''
        line = raw.removesuffix('\n')
        if ' | ' in line:
            head,sep,tail=line.partition(' | ')
            # Graph follows count; filenames are never touched.
            match=re.match(r'([ ]*[0-9]+[ ]*)([+\-]+)(.*)$', tail)
            if match:
                count, graph, suffix=match.groups()
                graph=re.sub(r'[+]+|[-]+', lambda m: (green if m.group()[0]=='+' else red)+m.group()+reset, graph)
                line=head+sep+count+graph+suffix
        else:
            line=re.sub(r'(\d+ insertions?\()\+(\))', lambda m:m.group(1)+green+'+'+reset+m.group(2), line)
            line=re.sub(r'(\d+ deletions?\()-(\))', lambda m:m.group(1)+red+'-'+reset+m.group(2), line)
        lines.append(line+ending)
    return ''.join(lines)


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
                   formatter_class=argparse.RawDescriptionHelpFormatter,
                   epilog='''Modules installables séparément (MODULE ou --only) :
  foundation | core          Base Ubuntu, Ansible, Docker et outils système
  services                   Jenkins et services essentiels
  plugins                    Extensions Jenkins et sécurité
  pipeline | cicd            Jenkins + plugins pour la chaîne CI/CD
  observability | metrics    Prometheus, Grafana, Node Exporter et alertes
  monitoring                 Alias de observability
  logs | logging             Loki + Alloy, OpenSearch + Data Prepper + Dashboards

Exemples :
  sudo autokom install logs             # choix RAM, puis Docker ou systemd
  sudo autokom install logs --backend loki --runtime systemd
  sudo autokom install --only logs --backend both
  sudo autokom install --only observability
  sudo autokom install --only pipeline
  autokom install --only logs --check
  sudo autokom logs use opensearch
  sudo autokom logs use loki
  autokom logs runtime                  # mode actif
  sudo autokom logs runtime systemd --apply  # migration contrôlée

Sans --only, le parcours historique (foundation/services/plugins/observability)
est conservé. Les logs sont installés séparément pour éviter une charge imprévue.''')
    i.add_argument('module', nargs='?', choices=STAGES+OPTIONAL_STAGES+tuple(INSTALL_ALIASES),
                   help='Module facultatif : ex. `autokom install logs`')
    i.add_argument('--check', action='store_true', help='Afficher le plan sans rien modifier')
    i.add_argument('--from', dest='start_at', choices=STAGES+OPTIONAL_STAGES, help='Reprendre à partir de cette phase')
    i.add_argument('--only', choices=STAGES+OPTIONAL_STAGES+tuple(INSTALL_ALIASES), help='Installer uniquement le module ou alias indiqué')
    i.add_argument('--backend', choices=('loki','opensearch','both'), help='Pour logs : installer un backend ou les deux (défaut : both)')
    i.add_argument('--runtime',choices=('docker','systemd'), help='Pour Loki/Alloy : Docker Compose ou services systemd')
    subcommand('status', help='État synthétique des composants', epilog='Exemple : autokom status')
    subcommand('check', help='Audit complet (équivalent script verify/stack.py)', epilog='Exemple : sudo autokom check')
    subcommand('config', help='Voir les ports, URLs et domaines, sans secrets', epilog='Exemple : autokom config')
    c = subcommand('configure', help='Configuration interactive ciblée (expert)',
                   epilog='Exemple : sudo autokom configure observability')
    c.add_argument('service', choices=('services', 'plugins', 'observability','logs'))
    lg = subcommand('logs', help='Superviser les logs et choisir le mode Docker ou systemd',
                    epilog='Exemples : autokom logs status | sudo autokom logs use opensearch')
    lg.add_argument('action', nargs='?', choices=('status','use','reconcile','verify','doctor','runtime','audit','grafana'), default='status')
    lg.add_argument('--apply', action='store_true', help='Appliquer la réconciliation du backend déjà installé')
    lg.add_argument('--check', action='store_true', help='Prévisualiser la réconciliation')
    lg.add_argument('backend', nargs='?', choices=('loki','opensearch','docker','systemd'))
    # Grafana plugin management remains independent from Jenkins 'plugins' phase.
    gp=subcommand('grafana',help='Gestion avancée de Grafana (plugins officiels)')
    gp.add_argument('section', nargs='?', choices=('plugins',), help='Catalogue de plugins officiel')
    gp.add_argument('grafana_args',nargs=argparse.REMAINDER)
    for alias in ('tools','services'):
        tp=subcommand(alias, help='Catalogue, configuration et exploitation des outils DevSecOps',
                      epilog='Exemples : autokom tools list --all | autokom services --all | autokom tools show loki')
        ops=tp.add_subparsers(dest='tool_cmd')
        ls=ops.add_parser('list', help='Lister les outils présents (ou --all)')
        ls.add_argument('--all', action='store_true', help='Inclure les outils absents, dont les moteurs de logs')
        sh=ops.add_parser('show', help='Chemins, environnement et documentation d’un outil')
        sh.add_argument('tool_name', nargs='?', help='Exemple : jenkins, loki, prometheus, mysql')
        sh.add_argument('--all', action='store_true', help='Sans outil : alias de `list --all`')
        sh.add_argument('-e','--environnements', dest='environments', action='store_true',
                        help='Afficher les noms de variables (sans valeurs)')
        sh.add_argument('-E','--environnements-values', dest='environments_values', action='store_true',
                        help='Afficher les valeurs, y compris les secrets (sudo + confirmation TTY)')
        for action in ('configure','restart','stop'):
            sp=ops.add_parser(action, help={'configure':'Configurer avec le module AUTOKOM',
                'restart':'Redémarrage systemd avec confirmation',
                'stop':'Arrêt systemd avec confirmation'}[action])
            sp.add_argument('tool_name')
        ch=ops.add_parser('change', help='Modifier une configuration AUTOKOM non sensible (simulation par défaut)')
        ch.add_argument('tool_name')
        ch.add_argument('settings', nargs='*', metavar='CLE=VALEUR')
        ch.add_argument('--apply', action='store_true', help='Valider et enregistrer après confirmation')
        # Natural syntax: autokom tools prometheus nano nginx-configuration.
        from tools_registry import SPECS
        for tool in SPECS:
            plugin_help = ('\nGestion des plugins :\n  autokom tools '+tool.name+' plugins [search|list|info|install|upgrade|remove]\n  autokom tools '+tool.name+' plugins -h\n' if tool.name in ('jenkins','grafana') else '')
            fp=ops.add_parser(tool.name,help=f'Inspecter les fichiers et variables de {tool.name}',
                              formatter_class=argparse.RawDescriptionHelpFormatter,
                              description=f'Fichiers et configuration de {tool.name}',
                              epilog='Exemples : autokom tools '+tool.name+' files ; autokom tools '+tool.name+' cat ALIAS'+plugin_help)
            fp.add_argument('file_action',choices=('files','cat','head','tail','nano','nano-end','env','auth'),nargs='?',
                            help='Action. Pour les plugins Jenkins/Grafana : « plugins » (menu séparé)')
            fp.add_argument('file_target',nargs='?',default='')
            fp.add_argument('file_extra',nargs='?',default='')
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
    # Plugin managers are service-specific but share AUTOKOM keyboard menus.
    # Delegate BEFORE argparse's generic tools/files grammar; all nested -h
    # actions belong to their dedicated parser. Preserve legacy file actions.
    if len(arguments)>=3 and arguments[0] in ('tools','services') and arguments[1] in ('jenkins','grafana') and arguments[2] in ('plugins','pluggins'):
        if arguments[1]=='jenkins':
            from jenkins_plugins import main as plugins_main, PluginError
            try:return plugins_main(arguments[3:])
            except (PluginError,RuntimeError,OSError) as exc:raise CLIError(str(exc)) from exc
        from grafana_plugins import main as plugins_main, PluginsError
        try:return plugins_main(arguments[3:])
        except (PluginsError,RuntimeError,OSError) as exc:raise CLIError(str(exc)) from exc
    # Calling a service without an action displays its local help, not a
    # cryptic positional-argument error. No mutation occurs.
    if len(arguments)==2 and arguments[0] in ('tools','services'):
        from tools_registry import TOOLS
        if arguments[1] in TOOLS:arguments.append('-h')
    # Ergonomic aliases, without swallowing unknown flags of individual tools.
    # `show --all` is an inventory (not hundreds of verbose details).
    if len(arguments) >= 2 and arguments[0] in ('tools', 'services'):
        if arguments[1] == '--all':
            arguments = arguments[:1] + ['list', '--all'] + arguments[2:]
    # Consistent local help, including legacy compound actions without changing
    # the historic positional command grammar.
    if '-h' in arguments or '--help' in arguments:
        if len(arguments)>1 and arguments[0]=='logs' and arguments[1] in ('status','use','reconcile','verify','doctor','runtime','audit','grafana'):
            action=arguments[1]
            specific=argparse.ArgumentParser(prog='autokom logs '+action,
                 description='Action journaux AUTOKOM : '+action)
            if action=='runtime':specific.add_argument('mode',nargs='?',choices=('docker','systemd'))
            if action=='use':specific.add_argument('backend',nargs='?',choices=('loki','opensearch'))
            if action in ('runtime','reconcile','grafana'):
                specific.add_argument('--check',action='store_true',help='Vérifier sans modifier')
                specific.add_argument('--apply',action='store_true',help='Appliquer après garde-fous et confirmation')
            specific.print_help();return 0
        if len(arguments)>3 and arguments[0] in ('tools','services') and arguments[2] in ('files','cat','head','tail','nano','nano-end','env','auth'):
            specific=argparse.ArgumentParser(prog='autokom '+arguments[0]+' '+arguments[1]+' '+arguments[2],
                description='Inspection et configuration de '+arguments[1]+' ; action '+arguments[2])
            specific.add_argument('alias',nargs='?',help='Alias du fichier / variable ; autokom tools '+arguments[1]+' files')
            specific.print_help();return 0
    # Preserve existing tools grafana files / cat ... commands.
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
        before = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
        old_head = before.stdout.strip() if before and before.returncode == 0 else ''
        try:
            # Avoid two simultaneous writers: Git output is printed after the
            # spinner is removed. The colored diffstat is generated separately.
            with Activity('Téléchargement et vérification Git', delay=0.2):
                result = subprocess.run(['git', '-C', str(ROOT), 'pull', '--ff-only', '--no-stat'],
                                        capture_output=True, text=True, timeout=180,
                                        check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CLIError('Mise à jour Git interrompue ou trop longue') from exc
        if result.stderr:
            print(result.stderr, end='' if result.stderr.endswith('\n') else '\n', file=sys.stderr)
        if result.stdout:
            print(result.stdout, end='' if result.stdout.endswith('\n') else '\n')
        if result.returncode:
            raise CLIError(f'git pull a échoué (code {result.returncode})')
        after = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
        new_head = after.stdout.strip() if after and after.returncode == 0 else ''
        if old_head and new_head and old_head != new_head:
            use_color = (sys.stdout.isatty() and 'NO_COLOR' not in os.environ
                         and os.environ.get('TERM', '') not in ('', 'dumb'))
            # Git's own colored diffstat: green insertions, red deletions.
            stat = command(['git', '-C', str(ROOT), 'diff',
                            '--color=always' if use_color else '--color=never',
                            '--stat', old_head, new_head], timeout=30)
            if stat and stat.returncode == 0 and stat.stdout:
                print(color_git_diffstat(stat.stdout, enabled=use_color), end='' if stat.stdout.endswith('\n') else '\n')
        return 0
    if a.cmd=='grafana':
        if a.section!='plugins':
            parser.print_help();return 0
        from grafana_plugins import main as grafana_plugins_main, PluginsError
        try:return grafana_plugins_main(a.grafana_args)
        except PluginsError as exc:raise CLIError(str(exc)) from exc
    if a.cmd == 'logs':
        from logging_stack import main as logs_main, LogError
        try:
            return logs_main([a.action] + ([a.backend] if a.backend else []) + (['--apply'] if a.apply else []) + (['--check'] if a.check else []))
        except LogError as exc:
            raise CLIError(str(exc)) from exc
    if a.cmd in ('tools','services'):
        from tools_registry import dispatch, ToolError
        try:
            return dispatch(a)
        except ToolError as exc:
            raise CLIError(str(exc)) from exc
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
                 'observability': 'observability.sh', 'logs':'logs.sh'}
        flags = ('--interactive',) if a.service not in ('services','logs') else ()
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
