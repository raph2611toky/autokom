# AUTOKOM — Résilience ACME via DNS-01 Cloudflare sur GCP

## Incident initial

Le 7 novembre 2026, sur GCP, `prometheus.ikom.mg` renvoie HTTP 404 lors des
précontrôles HTTP-01 sur `10.128.0.4` **et** `127.0.0.1`. Une série de patches
Nginx/HTTP-01 ne résout pas le problème. Cela ne prouve ni une erreur DNS ni
une fermeture de port : les requêtes arrivent à Nginx. Le blocage exact du
routage ou des permissions n'est pas établi sans `nginx -T` et `error.log`.

## Nouvelle stratégie

1. Reprendre Jenkins, Grafana, Prometheus et les choix enregistrés sans reset.
2. Si `/etc/autokom/secrets/cloudflare-dns.env` est root:0600, contient
   `CLOUDFLARE_ZONE_NAME`, `CLOUDFLARE_ZONE_ID` et `CLOUDFLARE_API_TOKEN`, et
   couvre le domaine, utiliser automatiquement **DNS-01 Cloudflare**.
3. Installer le plugin `python3-certbot-dns-cloudflare` avec APT si absent ;
   en cas d'indice APT obsolète, faire exactement une actualisation + nouvelle
   tentative. Refuser la mélange Snap/APT si Certbot ne voit toujours pas le plugin.
4. Stocker le fichier INI nécessaire aux renouvellements dans
   `/etc/letsencrypt/autokom/cloudflare-dns.ini` (dossier root:0700, fichier
   root:0600). Refuser les symlinks et ne jamais écraser un fichier étranger.
5. Appeler Certbot pour le domaine précis, avec `--cert-name <domaine>`,
   `--dns-cloudflare`, et une attente de propagation TXT de 60 secondes.
6. **La route Nginx HTTP-01 n'est plus testée** pour ce domaine. Le plugin
   crée et retire temporairement `_acme-challenge.<domaine>` en TXT dans la
   zone autoritaire Cloudflare. Les enregistrements A/AAAA/NS ne changent pas.
7. Si aucun token Cloudflare n'est configuré, garder le parcours HTTP-01
   préexistant, sans promettre qu'il fonctionne sur cet hôte.
8. Si le fichier Cloudflare existe mais est invalide, interrompre avec une
   cause explicite au lieu de revenir silencieusement à un HTTP-01 en échec.
9. Ne jamais supprimer les comptes, la configuration TLS existante ou les
   virtual hosts étrangers. La configuration HTTPS n'est publiée qu'après
   constat d'un certificat valide. Le timer Certbot gère les renouvellements.

Cette stratégie **n'efface pas les causes possibles du HTTP 404 Nginx**. Elle
élimine la dépendance au chemin HTTP lors de l'émission/renouvellement ACME,
mais une requête HTTPS de production doit encore être vérifiée après installation.

## Publication depuis Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-ACME-DNS01-CLOUDFLARE-RESILIENT-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_cloudflare_dns01_certbot_20261107 \
  tests.test_autokom_acme_retry_gcp_20261107 \
  tests.test_autokom_acme_webroot_404_20261107 \
  tests.test_autokom_gcp_nginx_ingress_preflight_20261107 \
  tests.test_autokom_observability_no_repeat_tls_20261107

git diff --check
git add scripts/lib/observability.py scripts/lib/observability_dns01.py \
  tests/test_autokom_cloudflare_dns01_certbot_20261107.py \
  docs/AUTOKOM-GCP-ACME-DNS01-CLOUDFLARE-RESILIENCE-2026-11-07.md
git commit -m "fix: use Cloudflare DNS-01 for resilient GCP observability HTTPS"
git push origin main
```

## Reprise sur GCP

```bash
sudo autokom update
sudo autokom install
```

Pour vérifier la présence du jeton **sans jamais afficher le secret** :

```bash
sudo stat -c '%U:%G %a %n' /etc/autokom/secrets/cloudflare-dns.env
sudo grep '^CLOUDFLARE_ZONE_NAME=' /etc/autokom/secrets/cloudflare-dns.env
```

Pour vérifier les sites après installation :

```bash
sudo nginx -t
sudo certbot certificates
sudo systemctl is-active nginx prometheus grafana-server jenkins
curl -4sSI --max-time 10 https://prometheus.ikom.mg/
curl -4sSI --max-time 10 https://grafana.ikom.mg/
sudo autokom status
```

Prometheus peut retourner `401` (protection Basic Auth) et Grafana `302`
(vers son formulaire de connexion) ; **un 404, un 502 ou un 503 persistant
signale encore un problème de routage ou d'amont et n'est pas un succès**.

## Références

- https://certbot-dns-cloudflare.readthedocs.io/en/stable/
- https://packages.ubuntu.com/python3-certbot

**Validation réalisée** : tests Python et régressions sur copie des sources.
**Validation non réalisée** : authentification réelle au Cloudflare de cette
VM, émission effective du certificat et accès HTTPS externe GCP.
