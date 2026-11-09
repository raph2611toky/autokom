#!/usr/bin/env python3
"""AUTOKOM — installation indépendante des outils IaC via Ansible.

Aucune authentification GCP, ressource cloud ou etat Terraform n'est modifiee.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/install/iac.sh"


class IACError(RuntimeError):
    pass


def _version(executable: str, args: list[str]) -> str:
    target = shutil.which(executable)
    if not target:
        return "absent"
    try:
        proc = subprocess.run([target, *args], check=False, capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "présent, version non lisible"
    if proc.returncode != 0:
        return "présent, vérification en échec"
    lines = proc.stdout.strip().splitlines()
    return lines[0].strip() if lines else "présent"


def show_status() -> int:
    print("AUTOKOM / Infrastructure as Code · outils locaux")
    print("  Terraform :", _version("terraform", ["version"]))
    print("  gcloud    :", _version("gcloud", ["version"]))
    print("  Authentification GCP : non modifiée par cette commande")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autokom iac",
        description="Outils IaC du serveur orchestrateur : Terraform et Google Cloud CLI",
        epilog=("Exemples :\n"
                "  sudo autokom iac install --check\n"
                "  sudo autokom iac install\n"
                "  sudo autokom iac install --only terraform\n"
                "  sudo autokom iac install --only gcloud\n"
                "  autokom iac status"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="action", required=True)
    install = commands.add_parser("install", help="Installer Terraform puis gcloud via Ansible")
    install.add_argument("--only", choices=("terraform", "gcloud"),
                         help="Installer uniquement un outil (captures séparées)")
    mode = install.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Prévisualiser les roles sans modifier Ubuntu")
    mode.add_argument("--apply", action="store_true",
                      help="Appliquer explicitement (identique à install sans option)")
    commands.add_parser("status", help="Afficher les versions locales, sans contacter GCP")
    args = parser.parse_args(argv)
    if args.action == "status":
        return show_status()
    if os.geteuid() != 0:
        raise IACError("Installation/verification Ansible : utiliser sudo autokom iac install")
    if not SCRIPT.is_file():
        raise IACError(f"Script d'installation introuvable : {SCRIPT}")
    component = args.only or "all"
    mode_value = "--check" if args.check else "--apply"
    print("AUTOKOM / IAC ·", "prévisualisation" if args.check else "installation", flush=True)
    print("  Composants :", "Terraform + Google Cloud CLI" if component == "all" else component, flush=True)
    result = subprocess.run(["bash", str(SCRIPT), mode_value, component], check=False)
    if result.returncode:
        raise IACError(f"Installation IaC interrompue (code {result.returncode}). Voir les tâches Ansible affichées.")
    if not args.check:
        show_status()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IACError as exc:
        print(f"  ✗ {exc}")
        raise SystemExit(1)
