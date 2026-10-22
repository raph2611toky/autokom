
#!/usr/bin/env bash
# AUTOKOM — Autonomous DevSecOps Platform
# Automation by IKOM
# Usage: sudo bash install.sh [--check|--apply]

set -Eeuo pipefail

MODE="${1:---check}"

REPO_URL="${AUTOKOM_REPO_URL:-https://github.com/raph2611toky/soutenance-projet-m2.git}"
INSTALL_DIR="${AUTOKOM_INSTALL_DIR:-/opt/autokom/source}"

info() {
    printf '\033[1;36m[AUTOKOM]\033[0m %s\n' "$*"
}

error() {
    printf '\033[1;31m[ERREUR]\033[0m %s\n' "$*" >&2
}

trap 'error "Echec à la ligne $LINENO."' ERR

# --- Validation ---
if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
    error "Usage: install.sh [--check|--apply]"
    exit 2
fi

if [[ "$EUID" -ne 0 ]]; then
    error "Exécuter avec sudo ou en root."
    exit 1
fi

source /etc/os-release

if [[ "${ID:-}" != "ubuntu" ]]; then
    error "Cette version cible Ubuntu."
    exit 1
fi

# --- Détection du dépôt local ---
PROJECT_DIR=""

if [[ -f "${BASH_SOURCE[0]}" ]]; then
    LOCAL_DIR="$(
        cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
    )"

    if [[ -f "$LOCAL_DIR/AUTOKOM/scripts/bootstrap/01-install-ansible.sh" ]]; then
        PROJECT_DIR="$LOCAL_DIR"
        info "Dépôt local détecté : $PROJECT_DIR"
    fi
fi

# --- Récupération de la plateforme ---
if [[ -z "$PROJECT_DIR" ]]; then
    info "[1/4] Vérification de Git"

    if ! command -v git >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends \
            git ca-certificates
    fi

    info "[2/4] Récupération du projet"

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Dépôt déjà présent : $INSTALL_DIR"
        info "Aucune mise à jour automatique."
    elif [[ -e "$INSTALL_DIR" ]]; then
        error "Le répertoire existe sans dépôt Git."
        exit 1
    else
        mkdir -p "$(dirname "$INSTALL_DIR")"
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi

    PROJECT_DIR="$INSTALL_DIR"
fi

# --- Chemins des composants ---
BANNER="$PROJECT_DIR/AUTOKOM/scripts/autokom-banner.sh"

ANSIBLE_SCRIPT="$PROJECT_DIR/AUTOKOM/scripts/bootstrap/01-install-ansible.sh"

FOUNDATION_SCRIPT="$PROJECT_DIR/AUTOKOM/scripts/install/02-install-foundation.sh"

# --- Vérification des modules ---
for script in "$ANSIBLE_SCRIPT" "$FOUNDATION_SCRIPT"; do
    if [[ ! -f "$script" ]]; then
        error "Module manquant : $script"
        exit 1
    fi
done

# --- Bannière AUTOKOM ---
if [[ -f "$BANNER" ]]; then
    bash "$BANNER"
else
    info "AUTOKOM - Automation by IKOM"
fi

# --- Installation des dépendances ---
info "[3/4] Installation / vérification d'Ansible"

bash "$ANSIBLE_SCRIPT"

# --- Installation de la fondation ---
info "[4/4] Fondation DevSecOps : $MODE"

bash "$FOUNDATION_SCRIPT" "$MODE"

# --- Résultat ---
if [[ "$MODE" == "--check" ]]; then
    info "Vérification terminée."
    info "Relancer avec --apply pour installer."
else
    info "Fondation AUTOKOM installée avec succès."
fi
