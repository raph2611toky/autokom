#!/usr/bin/env python3
"""AUTOKOM Core: mise à jour GitHub par navigateur et code, sans clé configurée.

Nécessite GitHub CLI (gh). Une configuration GitHub CLI temporaire est
utilisée pour chaque opération, puis supprimée. Aucune information
sensibile n'est ajoutée à Git, aux remotes ou aux fichiers de projet.

Fonction publique: update(ROOT, check=False), intégrée à autokom_cli.py.
Bootstrap si la CLI est cassée:
    sudo python3 /tmp/patch/scripts/lib/autokom_private_update.py \
         --repo /opt/autokom/source
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class UpdateError(RuntimeError):
    """Erreur de vérification ou de mise à jour de code AUTOKOM."""


OWNER_REPO = ("raph2611toky", "autokom")
UPDATE_BRANCH = "toky"  # Branche source des correctifs AUTOKOM (même si la branche locale s’appelle main).
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


def _run(args, root, *, env=None, timeout=120, check=True, interactive=False):
    try:
        proc = subprocess.run(
            list(args), cwd=str(root), env=env, check=False,
            stdout=None if interactive else subprocess.PIPE,
            stderr=None if interactive else subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Commande impossible ou interrompue : {args[0]}") from exc
    if check and proc.returncode:
        detail = "" if interactive else (proc.stderr or "").strip()[:500]
        raise UpdateError(f"Échec de {args[0]} (code {proc.returncode}). {detail}")
    return proc


def _git(root, *args, env=None, timeout=60):
    return _run(("git", *args), root, env=env, timeout=timeout).stdout.strip()


def _repository(root):
    if not root.is_dir() or not (root / ".git").exists():
        raise UpdateError(f"Dépôt Git AUTOKOM introuvable : {root}")
    origin = _git(root, "remote", "get-url", "origin")
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        origin,
    )
    if not match or tuple(x.lower() for x in match.groups()) != OWNER_REPO:
        raise UpdateError("Origin doit être https://github.com/raph2611toky/autokom.git (sans identifiant).")
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or ".." in branch or "@{" in branch:
        raise UpdateError("Nom de branche Git invalide ou HEAD détaché.")
    return branch


def _clean(root):
    # Évite d'effacer des modifications ou d'écraser des fichiers inconnus.
    state = _git(root, "status", "--porcelain", "--untracked-files=all")
    if state:
        raise UpdateError("Arbre de travail non propre : enregistrer ou retirer les modifications avant update.\n" + state[:600])


def _missing_in_commit(root, commit):
    missing = []
    for path in REQUIRED_CORE:
        found = _run(("git", "cat-file", "-e", f"{commit}:{path}"), root, check=False)
        if found.returncode:
            missing.append(path)
    return missing


def _check_protected_deletions(root, old, new):
    deleted = _git(root, "diff", "--name-only", "--diff-filter=D", old, new, "--", *PROTECTED_AREAS)
    if deleted:
        raise UpdateError(
            "Mise à jour refusée : suppression de fichiers Core/Ansible détectée.\n"
            + deleted[:1200]
            + "\nExaminer ce changement dans Git avant de modifier l'infrastructure."
        )


def _check_target(root, old, new):
    missing = _missing_in_commit(root, new)
    if missing:
        raise UpdateError(
            "Mise à jour refusée : la branche distante ne contient pas tous les fichiers Core indispensables :\n"
            + "\n".join("  - " + path for path in missing)
            + "\nRestaurer ces fichiers dans le dépôt GitHub AVANT de relancer autokom update."
        )
    content = _git(root, "show", f"{new}:scripts/lib/autokom_private_update.py")
    if "GH_CONFIG_DIR" not in content or "auth" not in content or "login" not in content:
        raise UpdateError("Mise à jour refusée : le module distant n'utilise pas l'authentification GitHub CLI attendue.")
    if old != new:
        lineage = _run(("git", "merge-base", "--is-ancestor", old, new), root, check=False)
        if lineage.returncode != 0:
            raise UpdateError("Historiques divergents : ni reset, ni merge forcé.")
        _check_protected_deletions(root, old, new)


def _temporary_auth_environment(folder):
    env = dict(os.environ)
    # Les jetons préexistants ne doivent jamais supprimer l'étape de validation par code.
    for name in (
        "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        "GH_HOST", "GH_ENTERPRISE_HOST", "GIT_ASKPASS", "SSH_ASKPASS",
        "GIT_TERMINAL_PROMPT", "GH_CONFIG_DIR",
    ):
        env.pop(name, None)
    env["GH_CONFIG_DIR"] = str(folder)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    return env


def update(root: Path, *, check=False):
    root = Path(root).resolve(strict=True)
    local_branch = _repository(root)
    branch = UPDATE_BRANCH
    _clean(root)
    print(f"AUTOKOM · Dépôt {OWNER_REPO[0]}/{OWNER_REPO[1]} · locale {local_branch} · source origin/{branch}", flush=True)
    current_missing = [path for path in REQUIRED_CORE if not (root / path).is_file()]
    if current_missing:
        print("ATTENTION : fichiers Core manquants dans la version actuelle :", flush=True)
        for name in current_missing:
            print("  - " + name, flush=True)
    if check:
        print("Simulation locale terminée. Aucun accès GitHub ni changement appliqué.", flush=True)
        return 0 if not current_missing else 2
    if shutil.which("gh") is None:
        raise UpdateError("GitHub CLI absent. Installer le paquet 'gh' sur le serveur, puis relancer.")
    if shutil.which("git") is None:
        raise UpdateError("Git est indisponible.")
    print("\nAuthentification par navigateur/code GitHub (aucun token à copier) :", flush=True)
    # Toujours un Device Flow frais, sans utiliser ni modifier ~/.config/gh.
    # --insecure-storage force le stockage dans GH_CONFIG_DIR, qui est éphémère et privé.
    with tempfile.TemporaryDirectory(prefix="autokom-gh-") as directory:
        os.chmod(directory, 0o700)
        env = _temporary_auth_environment(Path(directory))
        _run(
            ("gh", "auth", "login", "--hostname", "github.com", "--web",
             "--git-protocol", "https", "--insecure-storage", "--skip-ssh-key"),
            root, env=env, timeout=900, interactive=True,
        )
        print("\nAutorisation reçue. Vérification de la branche distante...", flush=True)
        # Le credential helper gh lit ses identifiants uniquement depuis le dossier temporaire.
        # Le helper global de Git est explicitement neutralisé.
        _run(
            ("git", "-c", "credential.helper=", "-c",
             "credential.helper=!gh auth git-credential", "fetch", "--no-tags", "origin", branch),
            root, env=env, timeout=180, interactive=True,
        )
    # En sortant du 'with', le jeton temporaire a été supprimé.
    old = _git(root, "rev-parse", "HEAD")
    new = _git(root, "rev-parse", "FETCH_HEAD")
    _check_target(root, old, new)
    _clean(root)
    if old == new:
        print("AUTOKOM est déjà à jour. Aucun service système modifié.")
        return 0
    print(f"Mise à jour contrôlée : {old[:10]} -> {new[:10]}", flush=True)
    _git(root, "merge", "--ff-only", new, timeout=120)
    remaining = [path for path in REQUIRED_CORE if not (root / path).is_file()]
    if remaining:
        raise UpdateError("La mise à jour a été appliquée mais des fichiers indispensables manquent : " + ", ".join(remaining))
    print("AUTOKOM Core rétabli / mis à jour. Aucun service n'a été redémarré.", flush=True)
    print("Exécuter 'sudo autokom app deploy' séparément pour le backend, après ses vérifications.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mise à jour AUTOKOM sécurisée avec GitHub CLI")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true", help="Prévalidation locale sans GitHub")
    args = parser.parse_args(argv)
    try:
        return update(args.repo, check=args.check)
    except (UpdateError, ValueError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
