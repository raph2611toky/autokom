#!/usr/bin/env python3
"""AUTOKOM Core — secure fast-forward update using GitHub CLI or Device Flow.

When AUTOKOM_UPDATE_GITHUB_USER explicitly names a login, use that account's
already stored GitHub CLI credential WITHOUT switching the global gh account.
If not configured, retain the original short-lived GitHub App Device Flow.
Never store a token in this repository, the remote URL, or Git CLI arguments.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class UpdateError(RuntimeError):
    """Erreur contrôlée (ne doit pas exposer de secrets)."""


OWNER_REPO = ("raph2611toky", "autokom")
UPDATE_BRANCH = "main"
CONFIG_FILE = Path("/etc/autokom/update.env")
REQUIRED_CORE = (
    "autokom",
    "install.sh",
    "scripts/lib/autokom_cli.py",
    "scripts/lib/autokom_private_update.py",
    "scripts/lib/autokom_security.py",
    "scripts/lib/services.py",
    "scripts/lib/tools_registry.py",
    "scripts/bootstrap/link-cli.sh",
    "scripts/bootstrap/preflight.sh",
    "scripts/install/foundation.sh",
    "scripts/install/services.sh",
    "scripts/verify/stack.py",
    "ansible/playbooks/foundation.yml",
)
PROTECTED_AREAS = ("scripts/", "ansible/", "autokom", "install.sh")
APPROVED_NON_CORE_REMOVALS = frozenset({"scripts/dev/platform-runtime.env.example"})
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


def _run(args, root, *, env=None, timeout=120, check=True):
    try:
        proc = subprocess.run(
            list(args), cwd=str(root), env=env, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Impossible d'exécuter la commande requise ou délai dépassé.") from exc
    if check and proc.returncode:
        if args[0] == "git" and "fetch" in args:
            raise UpdateError(
                "Git fetch refusé : vérifier l'accès de ton compte GitHub au dépôt "
                "raph2611toky/autokom (Contents: Read-only minimum)."
            )
        detail = (proc.stderr or "").strip()[:500]
        raise UpdateError(f"Commande {args[0]} échouée ({proc.returncode}) : {detail}")
    return proc


def _git(root, *args, env=None, timeout=60):
    return _run(("git", *args), root, env=env, timeout=timeout).stdout.strip()


def _repository(root):
    if not root.is_dir() or not (root / ".git").exists():
        raise UpdateError(f"Répertoire non reconnu comme dépôt AUTOKOM : {root}")
    origin = _git(root, "remote", "get-url", "origin")
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        origin,
    )
    if not match or tuple(part.lower() for part in match.groups()) != OWNER_REPO:
        raise UpdateError(
            "Origin doit être https://github.com/raph2611toky/autokom.git "
            "(sans mot de passe, token ni autres identifiants)."
        )
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or ".." in branch:
        raise UpdateError("Nom de branche invalide ou HEAD détaché")
    return branch


def _clean(root):
    state = _git(root, "status", "--porcelain", "--untracked-files=all")
    if state:
        raise UpdateError(
            "Dépôt modifié : mise à jour interrompue pour ne rien écraser. "
            "Vérifier avec git status --short.\n" + state[:600]
        )


def _missing_in_commit(root, commit):
    missing = []
    for path in REQUIRED_CORE:
        proc = _run(("git", "cat-file", "-e", f"{commit}:{path}"), root, check=False)
        if proc.returncode:
            missing.append(path)
    return missing


def _check_target(root, old, new):
    if not new or not re.fullmatch(r"[0-9a-f]{40,64}", new):
        raise UpdateError("Révision Git distante invalide")
    missing = _missing_in_commit(root, new)
    if missing:
        raise UpdateError(
            "La version distante est incomplète, mise à jour refusée :\n"
            + "\n".join("  - " + path for path in missing)
        )
    lineage = _run(("git", "merge-base", "--is-ancestor", old, new), root, check=False)
    if lineage.returncode:
        raise UpdateError("Historiques divergents : aucune réinitialisation ni fusion forcée")
    if old != new:
        deleted = _git(root, "diff", "--name-only", "--diff-filter=D", old, new,
                       "--", *PROTECTED_AREAS)
        blocked = [path for path in deleted.splitlines()
                   if path not in APPROVED_NON_CORE_REMOVALS]
        if blocked:
            raise UpdateError(
                "Mise à jour refusée : suppression de composants Core/Ansible :\n"
                + "\n".join(blocked)[:1400]
            )


def _config_value(name, path=CONFIG_FILE):
    """Read only named, non-secret account/client-id settings."""
    if not path.is_file():
        return ""
    if path.is_symlink():
        raise UpdateError("Le fichier de configuration GitHub ne doit pas être un lien symbolique")
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith(name + "="):
                return line.partition("=")[2].strip().strip('"\'')
    except (OSError, UnicodeError) as exc:
        raise UpdateError("Impossible de lire la configuration locale GitHub") from exc
    return ""


def _client_id_from_file(path=CONFIG_FILE):
    return _config_value("AUTOKOM_UPDATE_GITHUB_CLIENT_ID", path)


def _client_id():
    value = (os.environ.get("AUTOKOM_UPDATE_GITHUB_CLIENT_ID") or _client_id_from_file()).strip()
    if not value and sys.stdin.isatty():
        print("Aucun Client ID de GitHub App trouvé dans /etc/autokom/update.env.")
        value = input("Client ID public de la GitHub App de mise à jour : ").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{6,150}", value) or value.lower().startswith(("ton_", "votre_")):
        raise UpdateError(
            "Client ID GitHub App manquant ou invalide. Configurer "
            "AUTOKOM_UPDATE_GITHUB_CLIENT_ID dans /etc/autokom/update.env. "
            "Il s'agit d'un identifiant public, pas d'un Client secret."
        )
    return value


def _gh_user():
    user = (os.environ.get('AUTOKOM_UPDATE_GITHUB_USER') or _config_value('AUTOKOM_UPDATE_GITHUB_USER')).strip()
    if user and (not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', user) or '--' in user):
        raise UpdateError('AUTOKOM_UPDATE_GITHUB_USER : identifiant GitHub invalide')
    return user


def _gh_token_for_user(user):
    """Use a specific stored GH account, without switching or logging out others."""
    env = os.environ.copy()
    # GH_TOKEN / GITHUB_TOKEN override the gh credential store; never let them
    # redirect a named-user request to an unrelated CI identity.
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
        env.pop(key, None)
    try:
        proc = subprocess.run(
            ['gh', 'auth', 'token', '--hostname', 'github.com', '--user', user],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            check=False, timeout=15, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('GitHub CLI indisponible : installer gh et connecter le compte root') from exc
    secret = proc.stdout.strip()
    if proc.returncode or len(secret) < 24 or '\n' in secret or '\r' in secret:
        raise UpdateError(
            'Le compte GitHub ' + user + ' n\'est pas authentifié dans gh pour cet utilisateur Linux. '
            'Exécuter gh auth login --hostname github.com --git-protocol https --web.'
        )
    return secret


def _post_github(url, payload):
    request = Request(
        url, data=urlencode(payload).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "AUTOKOM-Core-Device-Flow/1.0",
        }, method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read(65536))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read(65536))
            if isinstance(body, dict) and body.get("error"):
                return body
        except (ValueError, UnicodeError, OSError):
            pass
        raise UpdateError(f"GitHub OAuth a répondu avec le statut HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError, TimeoutError) as exc:
        raise UpdateError("Communication HTTPS avec GitHub impossible") from exc


def _authorize(client_id):
    init = _post_github(DEVICE_URL, {"client_id": client_id})
    if init.get("error"):
        raise UpdateError(
            f"Device Flow GitHub refusé ({init['error']}). "
            "Vérifier que Device Flow est activé dans la GitHub App."
        )
    try:
        device_code = init["device_code"]
        user_code = init["user_code"]
        uri = init["verification_uri"]
        interval = max(5, int(init.get("interval", 5)))
        duration = min(900, int(init.get("expires_in", 900)))
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Réponse Device Flow GitHub invalide") from exc
    if uri != "https://github.com/login/device":
        raise UpdateError("URL d'autorisation GitHub inattendue")
    print("\nAUTOKOM — Autorisation GitHub", flush=True)
    print(f"  1. Ouvrir dans le navigateur : {uri}", flush=True)
    print(f"  2. Entrer ce code : {user_code}", flush=True)
    print("  3. Confirmer l'autorisation. AUTOKOM attend la réponse…\n", flush=True)
    deadline = time.monotonic() + duration
    while True:
        if time.monotonic() + interval > deadline:
            raise UpdateError("Code GitHub expiré. Relancer autokom update pour en recevoir un nouveau")
        time.sleep(interval)
        answer = _post_github(TOKEN_URL, {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if answer.get("access_token"):
            return answer["access_token"]
        error = answer.get("error", "unknown_error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error in ("expired_token", "bad_verification_code"):
            raise UpdateError("Autorisation GitHub expirée ou invalide. Relancer la commande")
        if error == "access_denied":
            raise UpdateError("Autorisation GitHub refusée dans le navigateur")
        if error == "incorrect_client_credentials":
            raise UpdateError("Client ID GitHub incorrect")
        raise UpdateError(f"Échec d'autorisation GitHub ({error})")


@contextmanager
def _git_auth_environment(token):
    with tempfile.TemporaryDirectory(prefix="autokom-git-") as folder:
        os.chmod(folder, 0o700)
        helper = Path(folder) / "askpass.sh"
        helper.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*|*username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *Password*|*password*) printf "%s\\n" "${AUTOKOM_GIT_TOKEN:-}" ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        env = os.environ.copy()
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS",
                    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            env.pop(key, None)
        env["GIT_ASKPASS"] = str(helper)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["AUTOKOM_GIT_TOKEN"] = token
        try:
            yield env
        finally:
            env.pop("AUTOKOM_GIT_TOKEN", None)


def _fetch_with_memory_token(root, branch, token):
    with _git_auth_environment(token) as env:
        _run(
            ("git", "-c", "credential.helper=", "-c", "credential.useHttpPath=true",
             "fetch", "--no-tags", "origin", f"refs/heads/{branch}"),
            root, env=env, timeout=180,
        )


def _print_git_changes(root, old, new):
    raw = _git(root, 'diff', '--numstat', '--no-renames', old, new)
    changes = []
    added = removed = 0
    for row in raw.splitlines():
        parts = row.split('\t', 2)
        if len(parts) != 3:
            continue
        a, d, path = parts
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            removed += int(d)
        changes.append((path, a, d))
    tty = sys.stdout.isatty()
    def color(value, shade):
        return f'\x1b[{shade}m{value}\x1b[0m' if tty else value
    print(f'\n  MODIFICATIONS GIT · {len(changes)} fichier(s) · origin/{UPDATE_BRANCH}', flush=True)
    shown = changes[:40]
    for path, a, d in shown:
        a_label = f'+{a}' if a != '-' else 'binaire'
        d_label = f'-{d}' if d != '-' else 'binaire'
        graph = (color('+' * min(int(a), 16), '32') if a.isdigit() else '') + (color('-' * min(int(d), 16), '31') if d.isdigit() else '')
        print('  ' + path + '  ' + graph + '  (' + color(a_label, '32') + ' ' + color(d_label, '31') + ')', flush=True)
    if len(changes) > len(shown):
        print(f'  … et {len(changes) - len(shown)} autres fichiers (git diff --stat pour les détails)', flush=True)
    print('  TOTAL : ' + color(f'+{added}', '32') + ' ' + color(f'-{removed}', '31') + ' lignes\n', flush=True)


def _sync_ha_workers_after_update(root, old, new, *,
                                  timer=Path('/etc/systemd/system/autokom-ha-worker-sync.timer'),
                                  production_root=Path('/opt/autokom/source')):
    """Opt-in rolling update. Never run on developers' machines by accident."""
    if root != production_root or not timer.is_file() or timer.is_symlink():
        return
    relevant = (
        'terraform/modules/gcp_regional_ha/files/worker-agent.py',
        'terraform/modules/gcp_regional_ha/files/worker-delivery-health.py',
        'scripts/lib/autokom_ha_worker_remote_apply.py',
        'scripts/lib/autokom_ha_worker_sync.py',
    )
    paths = set(_git(root, 'diff', '--name-only', old, new).splitlines())
    if not paths.intersection(relevant):
        print('AUTOKOM HA : fichiers workers inchangés ; timer conservé.')
        return
    script = root / 'scripts/lib/autokom_ha_worker_sync.py'
    if not script.is_file() or script.is_symlink():
        raise UpdateError('Synchroniseur HA absent après mise à jour ; rolling update non réalisé')
    print('AUTOKOM HA — synchronisation des workers du MIG…', flush=True)
    try:
        result = subprocess.run([sys.executable, str(script), '--apply'],
                                cwd=str(root), check=False, timeout=540)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('Core mis à jour ; synchronisation HA interrompue, le timer réessaiera') from exc
    if result.returncode:
        raise UpdateError(
            'Core mis à jour ; HA sync incomplète (retour ' + str(result.returncode) +
            '). Le timer HA réessaiera ; consulter autokom-ha-worker-sync.service.'
        )


def update(root: Path, *, check=False, preprod=False):
    root = Path(root).resolve(strict=True)
    branch = _repository(root)
    if preprod:
        if root != Path("/opt/autokom-preprod/source") or branch != "master":
            raise UpdateError("Préproduction : checkout master obligatoire dans /opt/autokom-preprod/source.")
        target = "master"
    else:
        if branch == "master" or root == Path("/opt/autokom-preprod/source"):
            raise UpdateError("Préproduction : utiliser update --preprod.")
        target = UPDATE_BRANCH
    _clean(root)
    missing = [name for name in REQUIRED_CORE if not (root / name).is_file()]
    print(
        f"AUTOKOM · Dépôt {OWNER_REPO[0]}/{OWNER_REPO[1]} · "
        f"locale {branch} · source origin/{target}", flush=True,
    )
    if missing:
        print("Fichiers Core actuellement absents :", flush=True)
        for name in missing:
            print("  - " + name, flush=True)
    if check:
        print("Diagnostic local terminé. Aucun accès réseau, aucun changement.")
        return 0 if not missing else 2
    if not shutil_which_git():
        raise UpdateError("Git n'est pas installé (prévu dans le playbook Ansible foundation)")
    user = _gh_user()
    if user:
        token = _gh_token_for_user(user)
        print('AUTOKOM — GitHub CLI : compte ' + user + ' (authentification conservée).', flush=True)
    else:
        client_id = _client_id()
        token = _authorize(client_id)
        print("Autorisation reçue. Vérification du dépôt distant…", flush=True)
    try:
        _fetch_with_memory_token(root, target, token)
    finally:
        del token
    old = _git(root, "rev-parse", "HEAD")
    new = _git(root, "rev-parse", "FETCH_HEAD")
    _check_target(root, old, new)
    _clean(root)
    if old == new:
        print("AUTOKOM est à jour. Aucun service modifié.")
        return 0
    print(f"Mise à jour contrôlée : {old[:10]} → {new[:10]}", flush=True)
    _print_git_changes(root, old, new)
    _git(root, "merge", "--ff-only", new, timeout=120, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    remaining = [name for name in REQUIRED_CORE if not (root / name).is_file()]
    if remaining:
        raise UpdateError("Mise à jour appliquée mais composants manquants : " + ", ".join(remaining))
    print("AUTOKOM Core restauré / mis à jour. Aucun service redémarré.")
    if preprod:
        print("Préproduction : utiliser scripts/preprod/manage.sh deploy après vérification.")
    else:
        print("Backend : utiliser autokom app deploy séparément après vérification.")
        _sync_ha_workers_after_update(root, old, new)
    return 0


def shutil_which_git():
    from shutil import which
    return which("git") is not None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mise à jour AUTOKOM : compte gh nominatif ou GitHub Device Flow")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true", help="Diagnostic local seulement")
    parser.add_argument("--preprod", action="store_true")
    args = parser.parse_args(argv)
    try:
        return update(args.repo, check=args.check, preprod=args.preprod)
    except (UpdateError, ValueError, OSError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Opération annulée, aucune mise à jour appliquée.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
