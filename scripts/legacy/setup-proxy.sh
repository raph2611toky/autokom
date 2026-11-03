#!/usr/bin/env bash
#
# setup-proxy.sh — Automatise la création d'un reverse proxy Nginx
#
# Usage:
#   ./setup-proxy.sh <domaine> <port> [--all] [--certbot] [--email adresse@mail.com] [--media chemin_local] [--media-url /media/]
#
# Exemples:
#   ./setup-proxy.sh app.example.com 3000
#   ./setup-proxy.sh app.example.com 3000 --all
#   ./setup-proxy.sh app.example.com 3000 --all --certbot --email moi@example.com
#   ./setup-proxy.sh app.example.com 3000 --media /var/lib/jenkins/app/etakalo/media
#   ./setup-proxy.sh app.example.com 3000 --media /var/lib/jenkins/app/etakalo/media --media-url /static-media/
#
set -euo pipefail

# ---------- Couleurs ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERREUR]${NC} $*" >&2; }

# ---------- Vérification root ----------
if [[ $EUID -ne 0 ]]; then
    error "Ce script doit être exécuté en root (sudo ./setup-proxy.sh ...)"
    exit 1
fi

# ---------- Usage ----------
usage() {
    echo "Usage: $0 <domaine> <port> [--all] [--certbot] [--email adresse@mail.com] [--media chemin_local] [--media-url /media/]"
    echo ""
    echo "  <domaine>     Nom de domaine à configurer (ex: app.example.com)"
    echo "  <port>        Port local vers lequel proxifier (ex: 3000)"
    echo "  --all         Transfère TOUS les headers utiles (websocket, IP, session, host, proto...)"
    echo "  --certbot     Active HTTPS automatiquement via Let's Encrypt (certbot)"
    echo "  --email       Email pour certbot (sinon --register-unsafely-without-email)"
    echo "  --media       Chemin LOCAL d'un dossier à servir directement par Nginx (ex: dossier media Django)"
    echo "  --media-url   Préfixe d'URL pour le dossier media (défaut: /media/)"
    exit 1
}

# ---------- Parsing des arguments ----------
DOMAIN=""
PORT=""
USE_ALL=false
USE_CERTBOT=false
EMAIL=""
MEDIA_PATH=""
MEDIA_URL="/media/"

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            USE_ALL=true
            shift
            ;;
        --certbot)
            USE_CERTBOT=true
            shift
            ;;
        --email)
            EMAIL="$2"
            shift 2
            ;;
        --media)
            MEDIA_PATH="$2"
            shift 2
            ;;
        --media-url)
            MEDIA_URL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done
set -- "${POSITIONAL[@]:-}"

DOMAIN="${1:-}"
PORT="${2:-}"

if [[ -z "$DOMAIN" || -z "$PORT" ]]; then
    error "Domaine et port sont obligatoires."
    usage
fi

# ---------- Validation du domaine ----------
if ! [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$ ]]; then
    error "Domaine invalide: $DOMAIN"
    exit 1
fi

# ---------- Validation du port ----------
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
    error "Port invalide: $PORT"
    exit 1
fi

# ---------- Validation du dossier media (si fourni) ----------
if [[ -n "$MEDIA_PATH" ]]; then
    if [[ ! -d "$MEDIA_PATH" ]]; then
        error "Le dossier media spécifié n'existe pas : $MEDIA_PATH"
        exit 1
    fi
    # Normalise pour que MEDIA_URL commence et finisse par /
    [[ "$MEDIA_URL" != /* ]] && MEDIA_URL="/$MEDIA_URL"
    [[ "$MEDIA_URL" != */ ]] && MEDIA_URL="$MEDIA_URL/"
fi

info "Domaine       : $DOMAIN"
info "Port backend  : $PORT"
info "Tous headers  : $USE_ALL"
info "Certbot       : $USE_CERTBOT"
if [[ -n "$MEDIA_PATH" ]]; then
    info "Media local   : $MEDIA_PATH"
    info "Media URL     : $MEDIA_URL"
fi

# ---------- Installation de Nginx si absent ----------
if ! command -v nginx &>/dev/null; then
    info "Nginx n'est pas installé, installation en cours..."
    apt-get update -y
    apt-get install -y nginx
    systemctl enable nginx
    systemctl start nginx
else
    info "Nginx est déjà installé."
fi

# ---------- Préparation des noms ----------
SAFE_NAME=$(echo "$DOMAIN" | tr '.' '_' | tr '-' '_')
UPSTREAM_NAME="${SAFE_NAME}_backend"
CONF_AVAILABLE="/etc/nginx/sites-available/${DOMAIN}.conf"
CONF_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}.conf"
WS_MAP_CONF="/etc/nginx/conf.d/websocket_upgrade_map.conf"

# ---------- Map pour le support websocket (nécessaire une seule fois globalement) ----------
if [[ "$USE_ALL" == true ]]; then
    if ! grep -rq 'map \$http_upgrade \$connection_upgrade' /etc/nginx/ 2>/dev/null; then
        info "Ajout du map global pour le support WebSocket..."
        cat > "$WS_MAP_CONF" <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF
    else
        info "Le map WebSocket existe déjà, on ne le duplique pas."
    fi
fi

# ---------- Construction du bloc de headers ----------
if [[ "$USE_ALL" == true ]]; then
    HEADERS_BLOCK=$(cat <<EOF
        # Headers standards
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;

        # Support WebSocket (upgrade de connexion)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        # Transfert intégral des cookies / sessions
        proxy_pass_header Set-Cookie;
        proxy_cookie_path / /;

        # Timeouts adaptés aux connexions longues (websocket, streaming)
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 60s;

        # Buffering désactivé, utile pour le streaming / SSE
        proxy_buffering off;
EOF
)
else
    HEADERS_BLOCK=$(cat <<EOF
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
EOF
)
fi

# ---------- Bloc location pour le dossier media (si fourni) ----------
MEDIA_BLOCK=""
if [[ -n "$MEDIA_PATH" ]]; then
    MEDIA_BLOCK=$(cat <<EOF

    location $MEDIA_URL {
        alias $MEDIA_PATH/;
        autoindex off;
        access_log off;
        expires 30d;
        add_header Cache-Control "public";
    }
EOF
)
fi

# ---------- Génération du fichier de config ----------
info "Génération de la configuration Nginx : $CONF_AVAILABLE"

cat > "$CONF_AVAILABLE" <<EOF
# Configuration générée automatiquement par setup-proxy.sh
# Domaine : $DOMAIN -> 127.0.0.1:$PORT

upstream $UPSTREAM_NAME {
    server 127.0.0.1:$PORT;
    keepalive 64;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    access_log /var/log/nginx/${DOMAIN}.access.log;
    error_log  /var/log/nginx/${DOMAIN}.error.log;
$MEDIA_BLOCK

    location / {
        proxy_pass http://$UPSTREAM_NAME;
        proxy_http_version 1.1;

$HEADERS_BLOCK
    }
}
EOF

# ---------- Activation du site ----------
ln -sf "$CONF_AVAILABLE" "$CONF_ENABLED"

# ---------- Test et rechargement de Nginx ----------
info "Test de la configuration Nginx..."
if nginx -t; then
    systemctl reload nginx
    info "Nginx rechargé avec succès."
else
    error "La configuration Nginx est invalide. Annulation."
    rm -f "$CONF_ENABLED"
    exit 1
fi

# ---------- Certbot ----------
if [[ "$USE_CERTBOT" == true ]]; then
    info "Vérification de Certbot..."

    if ! command -v certbot &>/dev/null; then
        info "Certbot n'est pas installé, installation en cours..."
        apt-get update -y
        apt-get install -y certbot python3-certbot-nginx
    else
        info "Certbot est déjà installé."
    fi

    # Vérification que le port 80 est bien accessible depuis l'extérieur
    info "Vérification que le domaine $DOMAIN répond bien sur le port 80..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "http://${DOMAIN}/" || echo "000")

    if [[ "$HTTP_CODE" == "000" ]]; then
        warn "Le domaine $DOMAIN n'est pas accessible depuis l'extérieur sur le port 80."
        warn "Vérifiez : le DNS pointe bien vers ce serveur, et le port 80 est ouvert"
        warn "dans le groupe de sécurité AWS (Security Group -> Inbound rules -> HTTP 80)."
        warn "Certbot n'a PAS été exécuté."
    else
        info "Le domaine répond (code HTTP $HTTP_CODE). Lancement de Certbot..."

        CERTBOT_ARGS=(--nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect)

        if [[ -n "$EMAIL" ]]; then
            CERTBOT_ARGS+=(-m "$EMAIL")
        else
            warn "Aucun email fourni (--email), utilisation de --register-unsafely-without-email."
            CERTBOT_ARGS+=(--register-unsafely-without-email)
        fi

        certbot "${CERTBOT_ARGS[@]}"
        info "Certificat SSL installé et Nginx configuré en HTTPS avec redirection automatique."
    fi
fi

info "Terminé. Le site $DOMAIN pointe maintenant vers 127.0.0.1:$PORT"
if [[ -n "$MEDIA_PATH" ]]; then
    info "Les fichiers de $MEDIA_PATH sont servis sur http://$DOMAIN$MEDIA_URL"
fi

