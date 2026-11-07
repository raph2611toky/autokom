#!/usr/bin/env python3
"""Safe, resumable Jenkins plugin bootstrap for AUTOKOM.

Uses the authenticated Jenkins CLI one plugin at a time. Refreshes the
controller's *signed* Update Center on failure and retries once. The only
archive fallback is the pinned, SHA-256 verified Pipeline Stage View release.
Never mutates existing plugins directly, disables Jenkins auth, or logs tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
import urllib.error
import urllib.request
import zipfile

# The official Jenkins directory lists this version + SHA256 as of 2026-11-07.
# If Jenkins is upgraded or a different version is required, *fail closed*;
# do not silently install an unverified or older plugin.
PINNED_STAGE_VIEW_VERSION = '2.41'
PINNED_STAGE_VIEW_SHA256 = 'fbc9dd724278f69b8c7638907363a609a8484cfaaa59321169468ee6c908952f'
PINNED_STAGE_VIEW_SOURCES = (
    'https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/pipeline-stage-view/2.41/pipeline-stage-view-2.41.hpi',
    'https://updates.jenkins.io/download/plugins/pipeline-stage-view/2.41/pipeline-stage-view.hpi',
)
LOG_DIR = Path('/var/log/autokom/jenkins')
PLUGIN_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,89}$')


def safe_reason(error):
    """Categorize errors without echoing server output, credentials, or URLs."""
    text = str(error).lower()
    if any(x in text for x in ('401', '403', 'unauthorized', 'forbidden')):
        return 'authentication_or_access_denied'
    if any(x in text for x in ('timed out', 'timeout', 'connection reset', 'connection refused')):
        return 'network_timeout'
    if any(x in text for x in ('unknownhost', 'name or service not known', 'dns')):
        return 'dns_resolution'
    if '404' in text or 'not found' in text:
        return 'plugin_or_artifact_missing'
    if 'sha256' in text or 'checksum' in text:
        return 'artifact_integrity'
    if 'dependency' in text or 'dependencies' in text:
        return 'plugin_dependency'
    if 'incompatible' in text or 'requires jenkins' in text:
        return 'plugin_incompatible'
    return 'jenkins_install_error'


class PluginAudit:
    """Root-private, append-only events; no CLI stdout/stderr or secrets."""
    def __init__(self, directory=None):
        directory = Path(directory) if directory is not None else LOG_DIR
        if directory.is_symlink():
            raise RuntimeError('Journal Jenkins : lien symbolique interdit')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = directory.stat()
        if st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise RuntimeError('Journal Jenkins : propriétaire ou permissions dangereuses')
        if st.st_mode & 0o077:
            # Only tighten the permissions of an already root-owned directory.
            directory.chmod(0o700)
        self.path = directory / ('plugins-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.jsonl')
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)

    def event(self, plugin, step, outcome, reason=None):
        if not PLUGIN_ID.fullmatch(plugin):
            raise ValueError('Identifiant de plugin non autorisé')
        event = {'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'plugin': plugin, 'step': step, 'outcome': outcome}
        if reason:
            event['reason'] = reason
        os.write(self.fd, (json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
        os.fsync(self.fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Trigger one signed Update Center refresh only after an install failure. Jenkins
# validates metadata signatures; no suppression of TLS or signature checks.
UPDATE_CENTER_REFRESH = '''import jenkins.model.Jenkins

def uc = Jenkins.get().getUpdateCenter()
if (uc.getSites().isEmpty()) throw new IllegalStateException('AUTOKOM_UPDATE_CENTER: no sites configured')
def results = uc.updateAllSites()
if (results.any { it.kind.toString() == 'ERROR' }) throw new IllegalStateException('AUTOKOM_UPDATE_CENTER: signed metadata refresh failed')
println('AUTOKOM_UPDATE_CENTER_REFRESHED')
'''


def refresh_update_center(cli, auth_file):
    text = cli(auth_file, ['groovy', '='], stdin=UPDATE_CENTER_REFRESH,
               timeout=240, status='Actualisation du centre de mises à jour Jenkins')
    if 'AUTOKOM_UPDATE_CENTER_REFRESHED' not in text:
        raise RuntimeError('Jenkins ne confirme pas la mise à jour du catalogue signé')


def _verified_download(url, archive, *, opener=None):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={'User-Agent': 'AUTOKOM-Jenkins-VerifiedPlugin/1'})
    with opener(request, timeout=75) as response:
        # Reject HTTPS -> HTTP downgrade, even when the initial URL uses HTTPS.
        final = response.geturl()
        if not final.startswith('https://'):
            raise RuntimeError('Téléchargement de plugin redirigé hors HTTPS')
        sha = hashlib.sha256()
        size = 0
        with archive.open('xb') as output:
            archive.chmod(0o600)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 16 * 1024 * 1024:
                    raise RuntimeError('Archive Jenkins anormalement grande')
                sha.update(chunk)
                output.write(chunk)
    if size < 1024 or sha.hexdigest() != PINNED_STAGE_VIEW_SHA256:
        raise RuntimeError('Checksum SHA256 Jenkins non conforme')
    with zipfile.ZipFile(archive) as z:
        manifest = z.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace')
    if not re.search(r'(?m)^Short-Name:\s*pipeline-stage-view\s*$', manifest):
        raise RuntimeError('Plugin téléchargé : identifiant incorrect')
    if not re.search(r'(?m)^Plugin-Version:\s*2\.41\s*$', manifest):
        raise RuntimeError('Plugin téléchargé : version incorrecte')


def stage_view_verified_fallback(cli, auth_file, audit, *, opener=None):
    """Only pinned 2.41 on a compatible core. Jenkins CLI installs the archive."""
    # Do not push a 2026 plugin onto an unknown or older Jenkins controller.
    try:
        version_text = cli(auth_file, ['groovy', '='],
            stdin='import jenkins.model.Jenkins; println("AUTOKOM_JENKINS_VERSION:" + Jenkins.VERSION)',
            timeout=60)
        match = re.search(r'AUTOKOM_JENKINS_VERSION:(\d+)\.(\d+)(?:\.(\d+))?', str(version_text or ''))
        if not match or tuple(int(g or 0) for g in match.groups()) < (2, 479, 1):
            audit.event('pipeline-stage-view', 'core-version', 'incompatible_or_unknown')
            return False
        audit.event('pipeline-stage-view', 'core-version', 'compatible')
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        audit.event('pipeline-stage-view', 'core-version', 'failed', safe_reason(error))
        return False
    with tempfile.TemporaryDirectory(prefix='autokom-stage-view-', dir='/run') as folder:
        archive = Path(folder) / 'pipeline-stage-view.hpi'
        for source in PINNED_STAGE_VIEW_SOURCES:
            archive.unlink(missing_ok=True)
            try:
                _verified_download(source, archive, opener=opener)
                audit.event('pipeline-stage-view', 'verified-archive', 'downloaded')
                cli(auth_file, ['install-plugin', str(archive), '-name', 'pipeline-stage-view'],
                    timeout=420, status='Installation vérifiée : Pipeline Stage View')
                audit.event('pipeline-stage-view', 'verified-archive-install', 'ok')
                return True
            except (OSError, RuntimeError, zipfile.BadZipFile, KeyError,
                    urllib.error.URLError, ValueError) as error:
                reason = safe_reason(error)
                audit.event('pipeline-stage-view', 'verified-archive', 'failed', reason)
    return False


def install_missing(plugins, cli, auth_file, *, output, log_dir=None, verified_fallback=stage_view_verified_fallback):
    """Install each selected missing plugin, report partial success and fail closed.

    Previously downloaded plugins are not removed or replaced. If one download
    fails, the other selected plugins may still be installed and reported, but
    the phase remains incomplete until the full selected profile is installed.
    """
    if any(not PLUGIN_ID.fullmatch(p) for p in plugins):
        raise ValueError('Identifiant de plugin Jenkins invalide')
    if not plugins:
        return []
    failures = []
    installed = []
    refreshed = False
    with PluginAudit(log_dir) as audit:
        output('›', f'Journal des plugins Jenkins : {audit.path}')
        for index, plugin in enumerate(plugins, start=1):
            output('›', f'Plugin {index}/{len(plugins)} : {plugin}')
            try:
                cli(auth_file, ['install-plugin', plugin], timeout=420,
                    status=f'Installation Jenkins : {plugin} ({index}/{len(plugins)})')
                installed.append(plugin)
                audit.event(plugin, 'install-plugin', 'ok')
                output('✓', f'Plugin {plugin} téléchargé')
                continue
            except (RuntimeError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
                audit.event(plugin, 'install-plugin', 'failed', safe_reason(error))
                output('⚠', f'Plugin {plugin} : première tentative échouée ; diagnostic automatique')

            if not refreshed:
                refreshed = True
                try:
                    refresh_update_center(cli, auth_file)
                    audit.event(plugin, 'refresh-update-center', 'ok')
                    output('✓', 'Catalogue Jenkins actualisé et validé')
                except (RuntimeError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
                    audit.event(plugin, 'refresh-update-center', 'failed', safe_reason(error))
                    output('⚠', 'Catalogue Jenkins : actualisation impossible ; reprise prudente')

            try:
                cli(auth_file, ['install-plugin', plugin], timeout=420,
                    status=f'Reprise de {plugin} après vérification')
                installed.append(plugin)
                audit.event(plugin, 'retry-install-plugin', 'ok')
                output('✓', f'Plugin {plugin} téléchargé après nouvelle tentative')
                continue
            except (RuntimeError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
                audit.event(plugin, 'retry-install-plugin', 'failed', safe_reason(error))

            if plugin == 'pipeline-stage-view':
                output('›', 'Pipeline Stage View : essai d’une archive officielle avec SHA-256 vérifié')
                if verified_fallback(cli, auth_file, audit):
                    installed.append(plugin)
                    output('✓', 'Pipeline Stage View téléchargé avec intégrité vérifiée')
                    continue

            failures.append(plugin)
            audit.event(plugin, 'install', 'incomplete')
            output('⚠', f'Échec confirmé : {plugin} (autres plugins poursuivis, journal conservé)')
        if failures:
            names = ', '.join(failures)
            raise RuntimeError(
                f'Installation Jenkins incomplète : {len(failures)} plugin(s) en échec : {names}. '
                f'{len(installed)} téléchargé(s) dans cette tentative. '
                f'Journal : {audit.path}. Aucun compte ni plugin existant supprimé.')
        return installed
