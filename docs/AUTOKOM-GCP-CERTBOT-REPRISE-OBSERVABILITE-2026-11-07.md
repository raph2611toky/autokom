# AUTOKOM — Reprise Certbot Prometheus/Grafana sur GCP (2026-11-07)

## Constat réel

Le journal utilisateur prouve que Prometheus, Grafana, les 4 dashboards et le compte administrateur Grafana ont été configurés. L'échec suivant est `certbot certonly --nginx` lors de l'installation HTTPS de `prometheus.ikom.mg`. Les règles UFW montrent TCP/80 et TCP/443 autorisés sur `ens4`, et `grafana.ikom.mg` résout vers `136.116.89.163`. Il **ne prouve pas** la cause précise de l'échec ACME : seul `tail -n 10` a été fourni et il ne contient pas le détail de validation.

## Correctif du code

- Accepte la reprise d'un fichier Nginx **strictement identique** au vhost temporaire `TLS PENDING` créé par AUTOKOM, activé par son lien symbolique attendu. Ne remplace aucun vhost étranger, différent, doublonné ou non géré.
- Réutilise un certificat ACME existant s'il est valide, ou retente Certbot si nécessaire.
- En cas d'échec ACME, conserve la barrière HTTP 503 (aucun service Prometheus/Grafana exposé en clair).
- Extrait uniquement le détail de l'échec **depuis le début de la nouvelle invocation** du journal Certbot et masque le jeton de challenge ; n'affiche pas le journal complet, les identifiants ou les clés.
- Aucun changement de domaine, identifiants, MySQL, Cloudflare, Jenkins, UFW, DNS ou règles GCP.

## Installation depuis Kali

```
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-CERTBOT-REPRISE-OBSERVABILITE-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_acme_retry_gcp_20261107 tests.test_observability tests.test_optional_letsencrypt_email_v15 tests.test_observability_install_flow_v14
git diff --check
git add scripts/lib/observability.py scripts/lib/observability_acme.py tests/test_autokom_acme_retry_gcp_20261107.py docs/AUTOKOM-GCP-CERTBOT-REPRISE-OBSERVABILITE-2026-11-07.md
git commit -m 'fix: resume observability ACME safely and expose exact failure'
git push origin main
```

## Reprise GCP

```
sudo autokom update
sudo autokom install
```

Le module fondation et Jenkins déjà conformes seront ignorés. Grafana/Prometheus seront revalidés et leurs secrets existants conservés. Le code ne corrige **pas** un pare-feu VPC externe bloquant ou une erreur DNS distante : il affiche la cause exacte et permet une reprise sans corruption d'état.

## Diagnostic précis avant une nouvelle tentative

Depuis GCP :

```
sudo grep -Ei 'Challenge failed|Detail:|Timeout during connect|Invalid response|unauthorized|rateLimited|CAA|DNS problem|Connection refused' /var/log/letsencrypt/letsencrypt.log | tail -25
sudo nginx -t
sudo ss -ltnp | grep -E ':(80|443)\b'
sudo certbot certificates
```

Depuis **Kali** (autre machine) :

```
curl -4Iv --connect-timeout 8 http://prometheus.ikom.mg/
curl -4Iv --connect-timeout 8 http://grafana.ikom.mg/
```

Lorsqu'une émission a déjà échoué, **ne pas répéter plusieurs tentatives de production sans avoir identifié la cause**, pour éviter les limites de validation ACME. La réussite d'un certificat Jenkins ne prouve pas la bonne configuration du vhost Prometheus.

### Pare-feu VPC GCP

Les règles UFW sur la VM sont distinctes des règles VPC Google Cloud. Inspecter les règles effectives applicables à l'instance (avec identité GCP autorisée) sans modifier ses autres flux. N'ouvrir **que** TCP/80 et TCP/443 s'ils sont effectivement manquants et après vérification des priorités / tags. Garder 9090, 3000 et 8080 sur loopback.

## Validation hors production

Tests de syntaxe et ciblés sur Nginx gated, réexécution après échec, non-remplacement d'un site étranger, diagnostic limité à la nouvelle tentative. Suite complète : 942 tests, 7 échecs et 5 erreurs préexistants identiques à 936 tests de référence. Aucune commande Certbot réelle vers GCP n'a été exécutée depuis cet environnement.
