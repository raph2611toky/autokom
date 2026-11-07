# AUTOKOM — GCP : DNS Jenkins automatisé avant Certbot

Date : 7 novembre 2026 — correctif différentiel de l'installation existante.

## Objectif

Sur GCP, `jenkins.ikom.mg` ne résout pas, tandis que `jenkins.ik0m.tech` (avec un zéro) résout déjà vers l'IPv4 GCP. Le domaine Jenkins sélectionné est une décision administrative enregistrée et conservée. Le module `services` doit préparer **uniquement le DNS Jenkins demandé**, sans modifier la racine de la zone ou les enregistrements d'autres applications.

## Modifications

- `scripts/lib/services.py` : récupération de l'IPv4 externe depuis le serveur de métadonnées officiel GCP, en refusant les adresses non publiques et les réponses sans l'en-tête `Metadata-Flavor: Google` ; pas de fallback silencieux vers une ancienne IPv4 AWS ni vers un service de découverte sortante. Synchronisation dans le module services **en mode apply seulement**, avant le contrôle DNS et Certbot. Attente bornée de convergence des résolveurs 1.1.1.1 et 8.8.8.8 ; contrôle de la résolution locale.
- `scripts/lib/autokom_dns_cloudflare.py` : idempotent `ensure_jenkins_a`, limité au hostname Jenkins sélectionné dans la zone indiquée par `CLOUDFLARE_ZONE_NAME`; crée un A DNS-only s'il n'existe pas, met à jour **seulement** le champ `content` d'un A existant (ne modifie pas `ttl` / `proxied`) et refuse tous les conflits CNAME/AAAA/NS, les enregistrements multiples, la racine et les noms DNS critiques. Aucune opération DELETE.
- `scripts/lib/services.py` : changement de l'URL Jenkins uniquement après réussite du reverse proxy et de TLS, et uniquement lorsque l'ancienne URL est gérée par un initialiseur AUTOKOM identifié. Jenkins étranger : conservé. Un redémarrage de Jenkins pour l'application de l'URL reste soumis au consentement de l'administrateur.
- Journaux JSONL existants : `cloudflare:jenkins`, `dns:jenkins`, `certbot`, `install` ; aucune valeur de jeton Cloudflare enregistrée.

Le changement DNS pendant l'installation n'est **pas** un service DDNS de surveillance continue. Sur GCP, réserver une IPv4 statique évite une modification future non contrôlée lors du remplacement d'une VM.

## Prérequis

- Ubuntu et fondation AUTOKOM/Jenkins/MySQL/Docker existantes ; aucun nouveau déploiement AWS.
- `/etc/autokom/secrets/cloudflare-dns.env` avec `CLOUDFLARE_ZONE_ID` (32 caractères hexadécimaux), `CLOUDFLARE_API_TOKEN` et `CLOUDFLARE_ZONE_NAME=ikom.mg`. Jeton limité aux permissions **DNS Read / DNS Write** sur la zone `ikom.mg`.
- Secrets sous root et permissions `0600`, dossier `/etc/autokom/secrets` en `0700`.
- GCP : ports publics TCP/80 et TCP/443 ouverts dans les règles du VPC et du pare-feu hôte pour ACME HTTP-01 et accès HTTPS ; **ne pas exposer Jenkins :8080 ou MySQL :3306**.
- Adresse IP actuelle à vérifier sur la VM GCP via les métadonnées (dans la session : `136.116.89.163`).

## Installation du patch (Kali)

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-JENKINS-CLOUDFLARE-AUTO-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_gcp_jenkins_cloudflare_20261107 tests.test_autokom_services_resume_spinner_20261107 tests.test_phase2
git diff --check
git add scripts/lib/services.py scripts/lib/autokom_dns_cloudflare.py tests/test_autokom_gcp_jenkins_cloudflare_20261107.py docs/AUTOKOM-GCP-CLOUDFLARE-JENKINS-2026-11-07.md
git commit -m "fix: reconcile selected Jenkins DNS on GCP before ACME"
git push origin main
```

## Reprise GCP

```bash
sudo chown root:root /etc/autokom/secrets/*.env
sudo chmod 600 /etc/autokom/secrets/*.env
sudo chmod 700 /etc/autokom/secrets
sudo autokom dns cloudflare env show
sudo autokom update
sudo autokom install
sudo autokom status
```

`sudo autokom install` reprend la sélection enregistrée (`jenkins.ikom.mg`, MySQL, Docker, Certbot) sans demander de recommencer l'assistant. En cas de DNS temporairement non convergé, l'état de services incomplet permet de relancer plus tard sans réinstallation. Les journaux sont dans `/var/log/autokom/install/services-*.jsonl`.

## Limites et sécurité

- Si l'enregistrement existe en CNAME ou AAAA, aucune suppression / conversion automatique : intervenir uniquement sur décision de l'administrateur.
- Si Cloudflare est proxifié, les résolveurs publics exposent les IP Cloudflare, pas la cible GCP : l'étape de vérification stricte demandera un mode DNS-only pour l'émission initiale ou une procédure ACME adaptée. AUTOKOM ne change pas silencieusement l'état `proxied`.
- L'installation ne modifie aucun domaine AWS ou autre service `ikom.mg`, ni les zones DNS critiques.
- Les variables d'application dans `/etc/autokom/secrets/runtime.env` doivent être validées séparément avant de migrer la publication des clients ; notamment `AUTOKOM_INGRESS_IPV4` ne doit plus désigner AWS. Aucune restauration ou synchronisation de données AWS n'est réalisée par ce correctif.
- Le certificat et le DNS fonctionnels nécessitent une validation réelle sur GCP. Les tests Python simulent Cloudflare, les métadonnées et les résolveurs : ils ne constituent pas une validation réseau de production.
