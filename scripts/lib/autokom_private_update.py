#!/usr/bin/env python3
"""GitHub App device authorization (one-time) + safe fast-forward private update.

No token is written to disk, git config or git remote. Standard library only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class UpdateError(RuntimeError):
    pass


def _run(*args, cwd=None, env=None, timeout=30, check=True):
    try:
        p = subprocess.run(list(args), cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('Commande Git interrompue ou indisponible.') from exc
    if check and p.returncode:
        raise UpdateError(f'Commande refusée : {args[0]} (code {p.returncode}). ' + p.stderr.strip()[:300])
    return p.stdout.strip()


def _api(url, payload=None, *, token=None, timeout=15):
    data = urllib.parse.urlencode(payload).encode() if payload is not None else None
    headers = {'Accept': 'application/json', 'User-Agent': 'AUTOKOM-Core-PrivateUpdate/1'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
        headers['X-GitHub-Api-Version'] = '2022-11-28'
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read(2_000_000).decode('utf-8'))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        raise UpdateError('Impossible de communiquer avec GitHub. Vérifier la connexion et les autorisations.') from exc


def _client_id():
    value = os.environ.get('AUTOKOM_UPDATE_GITHUB_CLIENT_ID', '')
    config = Path('/etc/autokom/update.env')
    if not value and config.is_file():
        for line in config.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'\s*AUTOKOM_UPDATE_GITHUB_CLIENT_ID\s*=\s*([A-Za-z0-9_]+)\s*', line)
            if match:
                value = match.group(1)
    if not re.fullmatch(r'[A-Za-z0-9_]{8,128}', value):
        raise UpdateError('Configurer AUTOKOM_UPDATE_GITHUB_CLIENT_ID dans /etc/autokom/update.env. '
                          'Activer Device Flow sur la GitHub App et l’installer sur le dépôt privé AUTOKOM.')
    return value


def _device_token(client_id):
    start = _api('https://github.com/login/device/code', {'client_id': client_id})
    if not all(start.get(x) for x in ('device_code', 'user_code', 'verification_uri')):
        raise UpdateError('GitHub n’a pas renvoyé de code de vérification valide.')
    print('\n  AUTHENTIFICATION GITHUB — MISE À JOUR PRIVÉE')
    print('  Ouvrir : ' + str(start['verification_uri']))
    print('  Saisir le code : ' + str(start['user_code']))
    print('  Le code est à usage unique. Il n’est pas enregistré dans AUTOKOM.\n', flush=True)
    interval = max(5, int(start.get('interval', 5)))
    deadline = time.monotonic() + min(900, int(start.get('expires_in', 900)))
    while time.monotonic() < deadline:
        time.sleep(interval)
        result = _api('https://github.com/login/oauth/access_token', {
            'client_id': client_id, 'device_code': start['device_code'],
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        })
        if result.get('access_token'):
            return result['access_token']
        problem = result.get('error')
        if problem == 'authorization_pending':
            continue
        if problem == 'slow_down':
            interval += 5
            continue
        if problem in ('expired_token', 'access_denied'):
            raise UpdateError('Autorisation expirée ou refusée. Relancer autokom update.')
        raise UpdateError('Autorisation GitHub impossible : ' + str(problem or 'réponse invalide'))
    raise UpdateError('Code GitHub expiré. Relancer autokom update.')


def _repo(root):
    if not (root / '.git').exists():
        raise UpdateError('Répertoire AUTOKOM non initialisé avec Git.')
    remote = _run('git', 'config', '--get', 'remote.origin.url', cwd=root)
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', remote)
    if not match:
        raise UpdateError('Pour la mise à jour par code, origin doit être une URL HTTPS github.com sans jeton.')
    owner, name = match.groups()
    if (owner.lower(), name.lower()) != ('raph2611toky', 'autokom'):
        raise UpdateError('Dépôt origin non autorisé pour AUTOKOM Core.')
    branch = _run('git', 'symbolic-ref', '--quiet', '--short', 'HEAD', cwd=root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch) or '..' in branch:
        raise UpdateError('Nom de branche invalide ou HEAD détaché.')
    if _run('git', 'status', '--porcelain', '--untracked-files=normal', cwd=root):
        raise UpdateError('Le dépôt contient des modifications locales. Commit/stash avant la mise à jour.')
    return owner, name, branch


def update(root: Path, *, check=False):
    root = root.resolve(strict=True)
    owner, name, branch = _repo(root)
    print(f'  Dépôt : {owner}/{name} · branche {branch}')
    if check:
        print('  Vérification locale réussie. Aucun accès GitHub ni modification.')
        return 0
    token = _device_token(_client_id())
    details = _api(f'https://api.github.com/repos/{owner}/{name}', token=token)
    if details.get('full_name', '').lower() != f'{owner}/{name}'.lower():
        raise UpdateError('Jeton sans accès autorisé au dépôt AUTOKOM.')
    # Git's credential prompt is handled with a disposable askpass executable.
    # The token exists only in the child environment; it never appears in argv,
    # remote.origin.url, Git's credential store, or the GitHub API logs.
    env = dict(os.environ)
    env['AUTOKOM_UPDATE_EPHEMERAL_TOKEN'] = token
    env['GIT_TERMINAL_PROMPT'] = '0'
    with tempfile.TemporaryDirectory(prefix='autokom-git-', dir='/tmp') as folder:
        askpass = Path(folder) / 'askpass.sh'
        askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" "x-access-token";; '
                           '*) printf "%s\\n" "$AUTOKOM_UPDATE_EPHEMERAL_TOKEN";; esac\n', encoding='utf-8')
        askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        env['GIT_ASKPASS'] = str(askpass)
        _run('git', '-c', 'credential.helper=', 'fetch', '--no-tags', '--', 'origin', branch,
             cwd=root, env=env, timeout=180)
    env.pop('AUTOKOM_UPDATE_EPHEMERAL_TOKEN', None)
    old = _run('git', 'rev-parse', 'HEAD', cwd=root)
    new = _run('git', 'rev-parse', 'FETCH_HEAD', cwd=root)
    if old == new:
        print('  AUTOKOM est déjà à jour.')
        return 0
    ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', old, new], cwd=root, check=False)
    if ancestor.returncode:
        raise UpdateError('Divergence des historiques. Mise à jour refusée (aucun reset ni merge forcé).')
    # Prevent a raced local worktree change between fetch and merge.
    if _run('git', 'status', '--porcelain', cwd=root):
        raise UpdateError('Des modifications locales sont apparues pendant la vérification : arrêt.')
    _run('git', '-c', 'user.name=AUTOKOM Update', 'merge', '--ff-only', new, cwd=root, timeout=120)
    print('  AUTOKOM mis à jour : ' + old[:10] + ' -> ' + new[:10])
    print('  Pour appliquer la nouvelle version du backend : sudo autokom app deploy')
    return 0
