# AUTOKOM — GCP : reverse proxy Nginx et HTTP-01 multi-interface (2026-11-07)

## Incident observé

`sudo autokom install` sur GCP reconnaissait correctement `autokom-prometheus-prometheus.ikom.mg.conf` (état `TLS PENDING`) puis interrompait la phase :

```
Préflight HTTP-01 prometheus.ikom.mg : Nginx renvoie HTTP 404
```

Le domaine résout vers `136.116.89.163`. Le port 80 est joignable depuis Let's Encrypt. Le fichier Nginx géré par AUTOKOM contient déjà un `location ^~ /.well-known/acme-challenge/` et son webroot `/var/www/autokom-acme`.

L'ancien précontrôle demandait la route exclusivement à `127.0.0.1:80` ; ce n'est pas nécessairement la route utilisée par le trafic entrant sur GCP. Un Nginx avec `listen 127.0.0.1:80` dans un autre virtual host et un site `listen 80` peut produire 404 sur la boucle locale et 200 sur l'interface réseau (scénario reproduit avec un vrai Nginx isolé). Un accès interdit au webroot peut également produire une réponse 404 ; ce n'est pas nécessairement un DNS ou un pare-feu mal configuré.

## Changements apportés

- Recherche uniquement locale de l'IPv4 source de la route par défaut, via `ip -j -4 route get 1.1.1.1`. Aucun trafic n'est envoyé à 1.1.1.1. Ignore Docker, WireGuard et loopback.
- Création d'un **fichier ACME temporaire aléatoire** ; vérification avec Host réel sur l'IPv4 de l'interface routée (destination sur laquelle GCP/AWS transmet le trafic NAT), puis diagnostic additionnel sur loopback.
- Si le réseau routé est connu, il fait foi : 404 sur loopback mais HTTP 200/contenu exact sur interface routée = préflight réussi ; 200 sur loopback mais 404 sur interface routée = préflight refusé.
- En cas de refus, journaux sans divulgation du jeton de validation et conseils ciblés pour `listen 80`, `server_name`, permissions et journal d'erreur Nginx. Aucun nouvel ordre Let's Encrypt n'est lancé.
- Vérification que **tous les répertoires ancêtres** du webroot sont traversables, sans modifier les permissions de répertoires système étrangers.
- Nouveau modèle HTTPS inspiré des bonnes pratiques du `scripts/legacy/setup-proxy.sh` fourni : upstream `127.0.0.1`, en-têtes `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Port` et `X-Forwarded-Proto`, timeouts adaptés ; WebSocket et streaming sur `/api/live/` pour Grafana seulement.
- Les configurations HTTPS AUTOKOM **de la version antérieure restent acceptées** pour une reprise TLS ; elles ne sont pas réécrites d'office. Les vhosts étrangers ne sont ni renommés ni écrasés.
- Port 80 : seulement le défi ACME et, après validation HTTPS, redirection. Avant validation, réponse 503 sur les routes ordinaires pour éviter d'exposer Grafana/Prometheus en HTTP.

## Installation du patch dans le dépôt sur Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-NGINX-PROXY-ACME-INGRESS-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_gcp_nginx_ingress_preflight_20261107 tests.test_autokom_acme_webroot_404_20261107 tests.test_autokom_acme_retry_gcp_20261107 tests.test_autokom_observability_no_repeat_tls_20261107 tests.test_observability tests.test_observability_tools_v16
git diff --check

git add scripts/lib/observability.py tests/test_autokom_gcp_nginx_ingress_preflight_20261107.py docs/AUTOKOM-GCP-NGINX-PROXY-ACME-INGRESS-2026-11-07.md
git commit -m 'fix: validate ACME via routed GCP NIC and harden observability proxy'
git push origin main
```

## Sur GCP

```bash
sudo autokom update
sudo autokom install
sudo autokom status
sudo certbot certificates
sudo nginx -t
```

Les choix SMTP, Grafana, Node Exporter et cAdvisor sont repris de l'état AUTOKOM existant. Aucun domaine Cloudflare, compte, certificat, service BIND, adresse AWS ou application Docker n'est modifié par ce patch.

## Si le précontrôle échoue encore

La sortie indiquera désormais **quelle adresse locale** reçoit HTTP 404 / 200. Pour analyser sans effacer de configuration :

```bash
ip -4 route get 1.1.1.1
sudo nginx -T 2>&1 | grep -E 'conflicting server name|listen .*80|server_name prometheus.ikom.mg'
sudo tail -n 50 /var/log/nginx/error.log
sudo ls -ld /var /var/www /var/www/autokom-acme /var/www/autokom-acme/.well-known /var/www/autokom-acme/.well-known/acme-challenge
```

Ne pas désactiver les autres sites, ni supprimer `default`, ni vider `/etc/letsencrypt`. Si un site tiers a le même `server_name`, l'intervention doit rester explicite. Ne pas exécuter `scripts/legacy/setup-proxy.sh` directement sur ces domaines : il écrirait un virtual host séparé et pourrait créer une collision ou exposer des endpoints en HTTP.

## Tests

Les tests Python vérifient l'analyse des IP routées, la distinction GCP NIC / loopback, le refus d'un mauvais contenu ACME, les droits des parents, l'absence de réinitialisation et les en-têtes Nginx. Un test d'intégration lance un vrai Nginx isolé et reproduit le cas loopback 404 / autre adresse locale 200. Ils ne remplacent pas la validation finale HTTPS sur GCP et via Let's Encrypt.
