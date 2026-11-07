# AUTOKOM — Correction ciblée du broker `domain.activate` et du journal de publication

## Incident reproduit

Date : 7 novembre 2026. La publication `3ff2d882` et les essais périodiques du réconciliateur renvoient `Broker request denied: TypeError code=AUTOKOM_BROKER_REFUSED`. Cloudflare a créé le DNS, mais aucun vhost public n'est finalisé.

La reproduction hors production de `handle('domain.activate', ...)` a levé :

```
TypeError: unsupported operand type(s) for +: 'PosixPath' and 'str'
```

Origine exacte dans `scripts/lib/autokom_platform_broker.py`, fonction `_proxy_apply` :

```python
owner=STATE/'domain-owner-'+host+'.txt'     # incorrect : Path + str
owner=STATE/('domain-owner-'+host+'.txt')   # correct : Path / str
```

## Changements

1. Corrige la construction du marqueur de propriété Nginx.
2. Journalise les phases `nginx_http`, `acme_certificate`, `nginx_https`, `ownership_commit` (sans paramètres ni secrets).
3. Classe les erreurs des commandes contrôlées : `AUTOKOM_NGINX_COMMAND_FAILED`, `AUTOKOM_CERTBOT_COMMAND_FAILED`, etc. Leur sortie brute ne figure ni dans journalctl, ni dans l'API.
4. Les exceptions Python imprévues renvoient `AUTOKOM_BROKER_INTERNAL_ERROR` avec uniquement fonction/numéro de ligne de ce module dans le journal administrateur.
5. Le worker enregistre dans **Journal technique** l'exécution Docker, DNS/HTTPS, commit/rollback et le code d'erreur sûr.
6. Ajoute les tests réels hors-ligne pour l'activation, la réexécution, l'absence de fuite de secrets, le contrôle de propriété, le rollback Certbot et les journaux.

## Installation depuis Kali

Le ZIP ne contient que quatre fichiers, à extraire à la racine du dépôt AUTOKOM **sur la version comportant le commit `cf9744ffdb`** (ou une version compatible vérifiée). Vérifier les modifications locales avant extraction.

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
unzip -o /home/raph/Downloads/AUTOKOM-DOMAIN-ACTIVATE-TYPEERROR-LOGS-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_domain_typeerror_and_logs_20261107 \
  tests.test_autokom_domain_publication_20261106 \
  tests.test_autokom_domain_selfheal_20261107 \
  tests.test_autokom_runtime_broker
git diff --check
git diff --stat
```

Puis réviser les quatre fichiers et publier :

```bash
git add scripts/lib/autokom_platform_broker.py \
  platform/backend/apps/deployments/runs/worker.py \
  tests/test_autokom_domain_typeerror_and_logs_20261107.py \
  docs/AUTOKOM-DOMAIN-ACTIVATE-TYPEERROR-TECHLOG-2026-11-07.md
git diff --cached --check
git commit -m "fix: repair domain activation Path type error and publish safe diagnostics"
git push origin main
```

## Activation autonome sur Ubuntu

```bash
sudo autokom update && \
sudo autokom runtime setup --check && \
sudo autokom runtime setup --apply && \
sudo autokom runtime doctor
```

Le broker/worker sont redémarrés par Ansible. **Ne pas relancer Jenkins et ne pas créer le vhost à la main.** Le service de réconciliation déjà installé est planifié et retente la publication DNS/HTTPS de la dernière version saine active, sous réserve que le serveur et le certificat puissent répondre correctement. Les publications historiquement échouées ne sont pas reclassées rétroactivement.

## Vérifications observables

Dans `journalctl -u autokom-platform-broker.service`, la phase et la catégorie de toute erreur seront indiquées sans sortie sensible. Dans l'interface, le Journal technique enregistrera davantage d'étapes pour les **nouvelles publications**. Pour les réparations automatiques sans nouvelle publication, le journal systemd du réconciliateur demeure la source d'observation ; les informations de la fiche Domaine seront mises à jour par le backend.

L'erreur Certbot préexistante de renouvellement de `loki.ik0m.tech` (`cert.pem` non symbolique) est **distincte** du `TypeError` : ce correctif ne modifie pas les certificats d'autres services.

## Validation hors production

- 33/33 tests ciblés réussis, dont 7 nouveaux tests.
- Avant : 777 tests Python, 11 échecs/erreurs.
- Après : 784 tests Python, **les mêmes 11 échecs/erreurs** et aucune nouvelle régression.
- Syntaxe Python validée.
- Pas de connexion réelle Cloudflare/Certbot/Nginx ni de publication sur Ubuntu effectuée lors de la génération.
