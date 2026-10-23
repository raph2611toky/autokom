#!/usr/bin/env bash
# AUTOKOM: read-only audit of WAN, UFW, Docker bindings and host listeners.
set -Eeuo pipefail
printf '\nAUTOKOM · Audit réseau (lecture seule)\n'
printf '%s\n' '──────────────────────────────────────────'
if command -v ip >/dev/null 2>&1; then
    ip -4 route get 1.1.1.1 2>/dev/null | head -n 1 || true
fi
printf '\n[ UFW ]\n'
if command -v ufw >/dev/null 2>&1; then
    ufw status verbose || true
else
    printf 'UFW absent\n'
fi
printf '\n[ TCP host — ports principaux ]\n'
if command -v ss >/dev/null 2>&1; then
    ss -lntp | grep -E ':(22|80|443|1521|2375|2376|3000|3306|33060|5432|6379|8080|9000|9090|9093|9100|9200)([[:space:]]|$)' || true
fi
printf '\n[ Publications Docker ]\n'
if command -v docker >/dev/null 2>&1; then
    docker ps --format 'table {{.Names}}\t{{.Ports}}' || true
fi
printf '\n[ Note ]\n'
printf '%s\n' 'Vérifier également les Security Groups AWS / pare-feu GCP.' \
               'Les ports publiés par Docker peuvent contourner UFW.' \
               'Aucune configuration n’a été modifiée.'
