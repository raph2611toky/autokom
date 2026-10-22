#!/usr/bin/env bash
#
# install-jenkins.sh — Installe Jenkins LTS + Java 21 sur Ubuntu
#
# Usage: sudo ./install-jenkins.sh
#
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERREUR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "Ce script doit être exécuté en root (sudo ./install-jenkins.sh)"
    exit 1
fi

info "Mise à jour des paquets..."
apt-get update -y

info "Installation de Java 21 (requis par Jenkins)..."
apt-get install -y fontconfig openjdk-21-jre

info "Ajout de la clé GPG officielle Jenkins..."
mkdir -p /etc/apt/keyrings
wget -O /etc/apt/keyrings/jenkins-keyring.asc \
    https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key

info "Ajout du dépôt APT Jenkins (LTS stable)..."
echo "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc]" \
    https://pkg.jenkins.io/debian-stable binary/ | tee \
    /etc/apt/sources.list.d/jenkins.list > /dev/null

info "Mise à jour de l'index des paquets..."
apt-get update -y

info "Installation de Jenkins..."
apt-get install -y jenkins

info "Activation et démarrage du service Jenkins..."
systemctl enable jenkins
systemctl start jenkins

sleep 5

if systemctl is-active --quiet jenkins; then
    info "Jenkins est actif et démarré avec succès."
else
    error "Jenkins ne semble pas démarré. Vérifiez : systemctl status jenkins"
    exit 1
fi

# Ouverture du port 8080 si UFW est actif
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
    info "UFW détecté et actif, ouverture du port 8080..."
    ufw allow 8080/tcp
fi

echo ""
info "======================================================"
info "Jenkins est installé !"
info "Accès : http://$(curl -s -4 ifconfig.me 2>/dev/null || echo VOTRE_IP):8080"
echo ""
info "Mot de passe administrateur initial :"
if [[ -f /var/lib/jenkins/secrets/initialAdminPassword ]]; then
    cat /var/lib/jenkins/secrets/initialAdminPassword
else
    warn "Fichier de mot de passe introuvable, Jenkins est peut-être encore en train de démarrer."
    warn "Réessayez dans quelques secondes : sudo cat /var/lib/jenkins/secrets/initialAdminPassword"
fi
info "======================================================"