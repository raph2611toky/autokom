#!/usr/bin/env bash
# AUTOKOM — portable bootstrap entrypoint
# curl -fsSL https://raw.githubusercontent.com/raph2611toky/autokom/main/install.sh | sudo bash -s -- --check
set -Eeuo pipefail

MODE='--check'
UPDATE=0
SERVICES=0
AUTOKOM_VERBOSE="${AUTOKOM_VERBOSE:-0}"
AUTOKOM_LIVE="${AUTOKOM_LIVE:-1}"
for argument in "$@"; do
    case "$argument" in
        --check|--apply) MODE="$argument" ;;
        --update) UPDATE=1 ;;
        --services) SERVICES=1 ;;
        --verbose) AUTOKOM_VERBOSE=1 ;;
        --no-live) AUTOKOM_LIVE=0 ;;
        *) printf 'Usage: install.sh [--check|--apply] [--services] [--update] [--verbose] [--no-live]\n' >&2; exit 2 ;;
    esac
done
export AUTOKOM_VERBOSE AUTOKOM_LIVE

(( EUID == 0 )) || { printf 'AUTOKOM doit être lancé avec sudo.\n' >&2; exit 1; }
[[ -r /etc/os-release ]] || { printf 'Système non reconnu.\n' >&2; exit 1; }
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || { printf 'AUTOKOM Foundation nécessite Ubuntu.\n' >&2; exit 1; }

REPO_URL='https://github.com/raph2611toky/autokom.git'
INSTALL_DIR="${AUTOKOM_INSTALL_DIR:-/opt/autokom/source}"
SRC="${BASH_SOURCE[0]:-}"
PROJECT_DIR=''

# bash -s / curl | bash have no reliable script path.
if [[ -n "$SRC" && -f "$SRC" ]]; then
    possible="$(cd -- "$(dirname -- "$SRC")" && pwd -P)"
    if [[ -f "$possible/scripts/lib/ui.sh" && -f "$possible/ansible/playbooks/01-foundation.yml" ]]; then
        PROJECT_DIR="$possible"
    fi
fi

printf '\n  AUTOKOM  ·  Initialisation du programme\n'
if [[ -z "$PROJECT_DIR" ]]; then
    if ! command -v git >/dev/null 2>&1; then
        printf '  + Installation préalable de Git...\n'
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends git ca-certificates
    fi
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
        case "$origin" in
            "$REPO_URL"|git@github.com:raph2611toky/autokom.git) ;;
            *) printf '  ✗ Dépôt existant non reconnu : %s\n' "$origin" >&2; exit 1 ;;
        esac
    elif [[ -e "$INSTALL_DIR" ]]; then
        printf '  ✗ Chemin déjà occupé : %s\n' "$INSTALL_DIR" >&2
        exit 1
    else
        mkdir -p -- "$(dirname -- "$INSTALL_DIR")"
        printf '  + Récupération du dépôt public...\n'
        git clone --quiet --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR"
    fi
    PROJECT_DIR="$INSTALL_DIR"
fi

if (( UPDATE )); then
    [[ -d "$PROJECT_DIR/.git" ]] || { printf '  ✗ Aucune copie Git à actualiser.\n' >&2; exit 1; }
    if [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
        printf '  ✗ Modifications locales présentes : mise à jour refusée.\n' >&2
        exit 1
    fi
    printf '  + Mise à jour explicite du dépôt...\n'
    git -C "$PROJECT_DIR" pull --ff-only --quiet
fi

# Load the shared presentation layer only from the verified project tree.
UI="$PROJECT_DIR/scripts/lib/ui.sh"
[[ -f "$UI" ]] || { printf '  ✗ Fichier de présentation manquant : %s\n' "$UI" >&2; exit 1; }
# shellcheck source=scripts/lib/ui.sh
source "$UI"
ak_init

BANNER="$PROJECT_DIR/scripts/autokom-banner.sh"
if [[ -f "$BANNER" ]]; then
    bash "$BANNER"
fi
ak_title
ak_kv 'Mode' "$([[ "$MODE" == '--check' ]] && echo 'Simulation' || echo 'Installation')"
ak_kv 'Version' "$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo locale)"
ak_kv 'Plateforme' "$PROJECT_DIR"

for file in \
    scripts/bootstrap/00-preflight.sh \
    scripts/bootstrap/01-install-ansible.sh \
    scripts/install/02-install-foundation.sh \
    scripts/verify/04-check-foundation.sh; do
    [[ -f "$PROJECT_DIR/$file" ]] || { ak_err "Module manquant : $file"; exit 1; }
done

ak_section '01 / 04  Environnement'
bash "$PROJECT_DIR/scripts/bootstrap/00-preflight.sh"

ak_section '02 / 04  Moteur d’automatisation'
bash "$PROJECT_DIR/scripts/bootstrap/01-install-ansible.sh"

ak_section '03 / 04  Fondation DevSecOps'
bash "$PROJECT_DIR/scripts/install/02-install-foundation.sh" "$MODE"

ak_section '04 / 04  Résultats'
if [[ "$MODE" == '--apply' ]]; then
    bash "$PROJECT_DIR/scripts/verify/04-check-foundation.sh"
    ak_ok 'Exécution du playbook terminée (voir vérification ci-dessus)'
else
    ak_info 'Simulation terminée : les paquets prévus ne sont pas encore installés.'
fi
if (( SERVICES )); then
    [[ -f "$PROJECT_DIR/scripts/install/05-services.sh" ]] || { ak_err 'Module Jenkins/DB absent'; exit 1; }
    ak_section '05 / 05  Jenkins et bases de données'
    bash "$PROJECT_DIR/scripts/install/05-services.sh" "$MODE"
fi
ak_finish
