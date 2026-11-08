#!/usr/bin/env python3
"""Verify/repair the *loaded* Jenkins Declarative Pipeline interpreter.

The pipeline XML can be accepted even when Jenkins cannot execute `pipeline {}`.
Only an authenticated local Jenkins API response counts as proof of readiness.
Plugin installs use Jenkins' signed Update Center through its official CLI;
no plugins or Jenkins security settings are replaced or disabled implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from urllib.parse import urlsplit

from cli_colors import pretty_print as say

REQUIRED = ('pipeline-model-definition',)
PLUGIN_API = '/pluginManager/api/json?tree=plugins[shortName,active,enabled,version]'

class PipelineError(RuntimeError):
    pass

@dataclass(frozen=True)
class PipelineState:
    missing: tuple[str, ...]
    disabled: tuple[str, ...]
    pending: tuple[str, ...]
    @property
    def ready(self):
        return not (self.missing or self.disabled or self.pending)


def inspect(env=None, request=None):
    """Read-only: never infer live readiness from installed .jpi archives."""
    from autokom_runtime_admin import load_env, jenkins_request
    env = load_env() if env is None else env
    request = jenkins_request if request is None else request
    try:
        status, payload = request(env, 'GET', PLUGIN_API, content_type='application/json')
    except Exception as exc:
        raise PipelineError('Jenkins : impossible de consulter les plugins actifs avec les identifiants runtime.') from exc
    if status in (401, 403):
        raise PipelineError(f'Jenkins refuse la liste des plugins (HTTP {status}) : compte API ou permissions à vérifier.')
    if status != 200:
        raise PipelineError(f'Jenkins PluginManager indisponible (HTTP {status}).')
    try:
        plugins = json.loads(payload)['plugins']
        if not isinstance(plugins, list):
            raise ValueError('plugins is not a list')
        found = {p['shortName']: p for p in plugins if isinstance(p,dict) and isinstance(p.get('shortName'),str)}
    except (ValueError,TypeError,KeyError,UnicodeError) as exc:
        raise PipelineError('API Jenkins : inventaire de plugins invalide ou incomplet.') from exc
    missing=[]; disabled=[]; pending=[]
    for name in REQUIRED:
        item=found.get(name)
        if item is None:
            missing.append(name)
        elif item.get('enabled') is False:
            disabled.append(name)
        elif item.get('active') is not True:
            pending.append(name)
    return PipelineState(tuple(missing),tuple(disabled),tuple(pending))


def _summary(status):
    if status.ready:
        say('  ✓ Jenkins Declarative Pipeline : interpréteur actif et vérifié par API')
        return
    if status.missing:
        say('  ! Plugins Jenkins absents : '+', '.join(status.missing))
    if status.disabled:
        say('  ✗ Plugins Jenkins désactivés : '+', '.join(status.disabled))
    if status.pending:
        say('  ! Plugins Jenkins installés mais non chargés : '+', '.join(status.pending))


def _verified_local_cli(env):
    """Refuse to send credentials to any address other than the local CLI target."""
    from jenkins_postinstall import JENKINS_URL
    configured=urlsplit(env.get('AUTOKOM_JENKINS_URL',''))
    local=urlsplit(JENKINS_URL)
    if (configured.scheme != 'http' or configured.hostname not in ('localhost','127.0.0.1')
            or configured.port != local.port or configured.path not in ('','/')
            or configured.username or configured.password or configured.query or configured.fragment):
        raise PipelineError('Jenkins doit correspondre au contrôleur local configuré pour le CLI ; aucun secret transmis.')
    if not env.get('AUTOKOM_JENKINS_USER') or not env.get('AUTOKOM_JENKINS_API_TOKEN'):
        raise PipelineError('Identifiants Jenkins absents de runtime.env.')


def _install(missing, env):
    from jenkins_postinstall import ensure_cli_jar, cli, transient_credentials, output
    from jenkins_plugin_recovery import install_missing
    _verified_local_cli(env)
    ensure_cli_jar(True)
    with transient_credentials(env['AUTOKOM_JENKINS_USER'], env['AUTOKOM_JENKINS_API_TOKEN']) as auth:
        install_missing(missing, cli, auth, output=output)


def _safe_restart(env):
    from jenkins_postinstall import cli, transient_credentials
    _verified_local_cli(env)
    with transient_credentials(env['AUTOKOM_JENKINS_USER'], env['AUTOKOM_JENKINS_API_TOKEN']) as auth:
        cli(auth, ['safe-restart'], timeout=65, status='Redémarrage Jenkins après chargement Declarative Pipeline')


def _wait_loaded(env, *, timeout=180):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic() < deadline:
        try:
            last=inspect(env)
            if last.ready:
                return last
        except PipelineError:
            pass # Expected while Jenkins is safely restarting.
        time.sleep(3)
    raise PipelineError('Plugin Jenkins encore inactif : redémarrage sûr en attente ou erreur de dépendances. '
                        'Consulter sudo autokom tools jenkins logs --lines 100 ; ne pas relancer un build.')


def reconcile(*, apply=False, env=None):
    """Idempotent, with read-only --check and safe Jenkins restart only if necessary."""
    from autokom_runtime_admin import load_env
    env=load_env() if env is None else env
    before=inspect(env)
    say('  ◈ AUTOKOM / JENKINS · PRÉREQUIS PIPELINE')
    _summary(before)
    if before.ready:
        return before
    if before.disabled:
        raise PipelineError('Plugin désactivé explicitement : AUTOKOM ne réactive pas un plugin administrativement désactivé.')
    if not apply:
        say('  › Réparation : sudo autokom runtime pipeline --apply')
        return before
    if os.geteuid() != 0:
        raise PipelineError('Utiliser sudo pour appliquer la réparation Jenkins.')
    if before.missing:
        say('  › Installation ciblée depuis le centre officiel Jenkins (dépendances gérées par Jenkins)')
        try:
            _install(list(before.missing),env)
        except Exception as exc:
            raise PipelineError('Installation du plugin Jenkins refusée ou incomplète : vérifier le catalogue signé, '
                                'la compatibilité Jenkins et les permissions API. Aucun plugin existant effacé.') from exc
    say('  › Jenkins : safe-restart (attend la fin des builds existants)')
    try:
        _safe_restart(env)
    except Exception as exc:
        raise PipelineError('Redémarrage sûr Jenkins non confirmé : vérifier les journaux Jenkins avant de relancer.') from exc
    result=_wait_loaded(env)
    say('  ✓ Jenkins a chargé Declarative Pipeline ; publication AUTOKOM autorisée')
    return result
