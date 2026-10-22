
#!/usr/bin/env bash
# AUTOKOM - Autonomous DevSecOps Platform
# Automation by IKOM
# Usage: sudo bash install.sh [--check|--apply]

set -Eeuo pipefail

MODE="${1:---check}"
REPO_URL="https://github.com/raph2611toky/autokom.git"
REPO_BRANCH="main"
INSTALL_DIR="${AUTOKOM_INSTALL_DIR:-/opt/autokom/source}"

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

trap 'warn "Installation interrompue (ligne $LINENO)."' ERR

# --------------------------------------
# Validation
# --------------------------------------

case "$MODE" in
    --check|--apply) ;;
    *) fatal "Usage: install.sh [--check|--apply]" ;;
esac

[[ "$EUID" -eq 0 ]] || fatal "Executer avec sudo."
[[ -r /etc/os-release ]] || fatal "OS non reconnu."

. /etc/os-release

[[ "${ID:-}" == "ubuntu" ]] || fatal \
    "AUTOKOM prend actuellement en charge Ubuntu."

# --------------------------------------
# Detection locale compatible curl|bash
# --------------------------------------

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
PROJECT_DIR=""

if [[ -n "$SCRIPT_SOURCE" && -f "$SCRIPT_SOURCE" ]]; then

    LOCAL_DIR="$(
        cd -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd -P
    )"

    if [[ -f "$LOCAL_DIR/scripts/bootstrap/01-install-ansible.sh" &&
          -f "$LOCAL_DIR/ansible/playbooks/01-foundation.yml" ]]; then

        PROJECT_DIR="$LOCAL_DIR"
        info "Source locale : $PROJECT_DIR"
    fi
fi

# --------------------------------------
# Recuperation du projet GitHub
# --------------------------------------

if [[ -z "$PROJECT_DIR" ]]; then

    info "Source distante : $REPO_URL"

    if ! command -v git >/dev/null 2>&1; then
        info "Installation de Git..."

        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends \
            git ca-certificates
    fi

    if [[ -d "$INSTALL_DIR/.git" ]]; then

        ORIGIN="$(
            git -C "$INSTALL_DIR" remote get-url origin \
                2>/dev/null || true
        )"

        case "$ORIGIN" in
            "$REPO_URL"|git@github.com:raph2611toky/autokom.git)
                info "Depot deja present : $INSTALL_DIR"
                warn "Aucune mise a jour automatique."
                ;;
            *)
                fatal "Depot existant non reconnu : $ORIGIN"
                ;;
        esac

    elif [[ -e "$INSTALL_DIR" ]]; then

        fatal "Repertoire occupe : $INSTALL_DIR"

    else

        info "Clonage AUTOKOM..."

        mkdir -p -- "$(dirname -- "$INSTALL_DIR")"

        git clone \
            --depth 1 \
            --branch "$REPO_BRANCH" \
            --single-branch \
            "$REPO_URL" \
            "$INSTALL_DIR"
    fi

    PROJECT_DIR="$INSTALL_DIR"
fi

# --------------------------------------
# Chemins des modules
# --------------------------------------

BANNER="$PROJECT_DIR/scripts/autokom-banner.sh"

PREFLIGHT="$PROJECT_DIR/scripts/bootstrap/00-preflight.sh"

BOOTSTRAP="$PROJECT_DIR/scripts/bootstrap/01-install-ansible.sh"

FOUNDATION="$PROJECT_DIR/scripts/install/02-install-foundation.sh"

VERIFY="$PROJECT_DIR/scripts/verify/04-check-foundation.sh"

for REQUIRED in "$BOOTSTRAP" "$FOUNDATION"; do
    [[ -f "$REQUIRED" ]] || fatal \
        "Module manquant : $REQUIRED"
done

# --------------------------------------
# Banniere AUTOKOM
# --------------------------------------

if [[ -f "$BANNER" ]]; then
    bash "$BANNER"
else
    info "AUTOKOM - Automation by IKOM"
fi

if [[ -d "$PROJECT_DIR/.git" ]]; then
    REV="$(
        git -C "$PROJECT_DIR" rev-parse --short HEAD \
            2>/dev/null || true
    )"
    [[ -z "$REV" ]] || info "Revision : $REV"
fi

# --------------------------------------
# Diagnostic
# --------------------------------------

info "[1/4] Diagnostic du serveur"

if [[ -f "$PREFLIGHT" ]]; then
    bash "$PREFLIGHT"
else
    warn "Diagnostic absent."
fi

# --------------------------------------
# Bootstrap Ansible
# --------------------------------------

info "[2/4] Verification / installation Ansible"

bash "$BOOTSTRAP"

# --------------------------------------
# Fondation DevSecOps
# --------------------------------------

info "[3/4] Installation Foundation : $MODE"

bash "$FOUNDATION" "$MODE"

# --------------------------------------
# Verification
# --------------------------------------

info "[4/4] Verification des outils"

if [[ "$MODE" == "--apply" && -f "$VERIFY" ]]; then

    bash "$VERIFY"

elif [[ "$MODE" == "--check" ]]; then

    info "Simulation Ansible terminee."
fi

# --------------------------------------
# Resultat
# --------------------------------------

if [[ "$MODE" == "--check" ]]; then

    info "Simulation terminee."
    info "Relancer avec --apply pour installer."
    warn "Git et Ansible peuvent etre installes en mode --check."

else

    info "Installation de la fondation terminee."
fi
