
#!/usr/bin/env bash
# AUTOKOM - Autonomous DevSecOps Platform
# Automation by IKOM
# Usage: sudo bash install.sh [--check|--apply]

set -Eeuo pipefail

readonly REPO_URL="https://github.com/raph2611toky/autokom.git"
readonly REPO_REF="${AUTOKOM_REF:-main}"
readonly INSTALL_DIR="${AUTOKOM_INSTALL_DIR:-/opt/autokom/source}"

MODE="${1:---check}"

info() {
    printf '\033[1;36m[AUTOKOM]\033[0m %s\n' "$*"
}

warn() {
    printf '\033[1;33m[ATTENTION]\033[0m %s\n' "$*" >&2
}

fatal() {
    printf '\033[1;31m[ERREUR]\033[0m %s\n' "$*" >&2
    exit 1
}

trap 'warn "Echec pendant l installation (ligne $LINENO)."' ERR

# -----------------------------------------
# 1. Validation
# -----------------------------------------

case "$MODE" in
    --check|--apply) ;;
    *) fatal "Usage : sudo bash install.sh [--check|--apply]" ;;
esac

[[ "$EUID" -eq 0 ]] || fatal "Lancer avec sudo ou en root."
[[ -r /etc/os-release ]] || fatal "/etc/os-release introuvable."

source /etc/os-release

[[ "${ID:-}" == "ubuntu" ]] || fatal \
    "AUTOKOM Foundation cible actuellement Ubuntu."

# -----------------------------------------
# 2. Detection du depot local
# -----------------------------------------

PROJECT_DIR=""

if [[ -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(
        cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P
    )"

    if [[ -f "$SCRIPT_DIR/scripts/bootstrap/01-install-ansible.sh" &&
          -f "$SCRIPT_DIR/ansible/playbooks/01-foundation.yml" ]]; then

        PROJECT_DIR="$SCRIPT_DIR"
        info "Source locale reconnue : $PROJECT_DIR"
    fi
fi

# -----------------------------------------
# 3. Recuperation AUTOKOM depuis GitHub
# -----------------------------------------

if [[ -z "$PROJECT_DIR" ]]; then
    info "Installation depuis le depot public GitHub."

    if ! command -v git >/dev/null 2>&1; then
        info "Installation du pre-requis Git..."

        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends \
            git ca-certificates
    fi

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        existing_remote="$(
            git -C "$INSTALL_DIR" remote get-url origin || true
        )"

        case "$existing_remote" in
            "$REPO_URL"|git@github.com:raph2611toky/autokom.git)
                info "AUTOKOM deja present dans $INSTALL_DIR"
                info "Aucune mise a jour automatique."
                ;;
            *)
                fatal "Depot existant non reconnu : $existing_remote"
                ;;
        esac

    elif [[ -e "$INSTALL_DIR" ]]; then
        fatal "Chemin occupe : $INSTALL_DIR"

    else
        info "Clonage du depot AUTOKOM..."

        mkdir -p -- "$(dirname -- "$INSTALL_DIR")"

        git clone \
            --depth 1 \
            --branch "$REPO_REF" \
            --single-branch \
            "$REPO_URL" \
            "$INSTALL_DIR"
    fi

    PROJECT_DIR="$INSTALL_DIR"
fi

# -----------------------------------------
# 4. Composants AUTOKOM
# -----------------------------------------

BANNER="$PROJECT_DIR/scripts/autokom-banner.sh"
PREFLIGHT="$PROJECT_DIR/scripts/bootstrap/00-preflight.sh"
BOOTSTRAP="$PROJECT_DIR/scripts/bootstrap/01-install-ansible.sh"
FOUNDATION="$PROJECT_DIR/scripts/install/02-install-foundation.sh"
VERIFY="$PROJECT_DIR/scripts/verify/04-check-foundation.sh"

for required in "$BOOTSTRAP" "$FOUNDATION"; do
    [[ -f "$required" ]] || fatal \
        "Composant manquant : $required"
done

# -----------------------------------------
# 5. Banniere
# -----------------------------------------

if [[ -f "$BANNER" ]]; then
    bash "$BANNER"
else
    info "AUTOKOM - Automation by IKOM"
fi

if [[ -d "$PROJECT_DIR/.git" ]]; then
    info "Version locale : $(
        git -C "$PROJECT_DIR" rev-parse --short HEAD \
            2>/dev/null || echo inconnue
    )"
fi

# -----------------------------------------
# 6. Diagnostic
# -----------------------------------------

info "[1/4] Diagnostic non destructif"

if [[ -f "$PREFLIGHT" ]]; then
    bash "$PREFLIGHT"
else
    warn "Script preflight absent."
fi

# -----------------------------------------
# 7. Installation Ansible
# -----------------------------------------

info "[2/4] Verification/installation Ansible"

bash "$BOOTSTRAP"

# -----------------------------------------
# 8. Fondation DevSecOps
# -----------------------------------------

info "[3/4] Fondation DevSecOps : $MODE"

bash "$FOUNDATION" "$MODE"

# -----------------------------------------
# 9. Verification
# -----------------------------------------

info "[4/4] Controle des outils"

if [[ "$MODE" == "--apply" && -f "$VERIFY" ]]; then
    bash "$VERIFY"

elif [[ "$MODE" == "--check" ]]; then
    info "Mode simulation : aucune application du playbook."
fi

# -----------------------------------------
# 10. Resultat
# -----------------------------------------

if [[ "$MODE" == "--check" ]]; then
    info "Simulation terminee."
    info "Pour installer : sudo bash install.sh --apply"
    warn "Le mode --check peut installer Git et Ansible."
else
    info "Fondation executee."
    info "Verifier les outils avant de poursuivre."
fi
