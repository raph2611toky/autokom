# AUTOKOM — Domaines HTTPS automatiques et paramètres de projets

## Portée

Ce lot est **différentiel** par rapport à la version `2699f7aa63` (sources reçues, plus le correctif projets/sites/domaines précédent). Les autres fichiers de production ne sont pas modifiés. Aucun secret `.env`, jeton Cloudflare ou clé privée n'est inclus.

Il répare la situation observée : publication `0bba85bc` réussie, `portfolio.auto.ikom.mg` réservé mais non activé, enregistrement A absent de Cloudflare, aucun vhost public Nginx. Avant ce correctif, seul le parcours d'une **nouvelle** publication Jenkins activait automatiquement le domaine. Une mise à jour du code ne rejouait pas ce parcours pour un site déjà en ligne.

## Nouveau fonctionnement

1. `autokom-domain-reconciler.timer` lance périodiquement, sous le compte de service `autokom`, une commande Django indépendante de Jenkins.
2. Celle-ci cible uniquement les projets actifs dont le backend a une publication ACTIVE et une application RUNNING en production. Elle ignore les environnements en cours de déploiement.
3. L'inspection du broker vérifie l'appartenance du vhost, le port de la publication, la configuration TLS, le certificat présent et la résolution DNS publique.
4. Si le domaine géré est en attente ou si sa configuration manque, le worker demande au broker de créer ou vérifier un A Cloudflare **DNS only** vers `AUTOKOM_INGRESS_IPV4`, d'attendre la propagation DNS, puis d'activer le vhost Nginx et le certificat Certbot.
5. Les échecs sont conservés sous forme de codes `AUTOKOM_*` (sans divulguer de secrets), réessayés avec temporisation de 5 minutes. La publication Docker active n'est pas arrêtée et le pipeline Jenkins n'est pas relancé.
6. L'URL n'est proposée comme lien public qu'une fois le statut HTTPS actif confirmé par Django. Le frontend actualise régulièrement la page du projet et la gestion des domaines.

## Prérequis de production (non contournables)

- `AUTOKOM_MANAGED_DOMAIN_SUFFIX` doit correspondre dans **backend.env** et **runtime.env** (pour les domaines actuels : `auto.ikom.mg`).
- Le broker doit lire la bonne zone Cloudflare (`CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_ZONE_NAME=ikom.mg`) et un jeton autorisé à lire et écrire les enregistrements DNS. Son unité charge `/etc/autokom/secrets/cloudflare-dns.env` : l'existence d'un jeton utilisable par **une autre commande** ne garantit pas qu'il est chargé par ce service.
- `AUTOKOM_INGRESS_IPV4` doit correspondre à l'IPv4 publique du serveur, et `AUTOKOM_ACME_EMAIL` doit être valide.
- TCP/80 et TCP/443 doivent être accessibles publiquement pour Certbot HTTP-01 et les sites. Aucun firewall ni DNS extérieur n'est modifié par la réconciliation.
- Si un CNAME ou un A différent existe déjà sur le nom, le broker refuse sa modification. La réconciliation ne désactive ni Cloudflare, ni le réseau Docker `--internal`, ni les contrôles de propriété des vhosts.

## Vérification dans Kali avant commit

Extraire le ZIP **à la racine du dépôt AUTOKOM** sur une branche de travail propre, puis :

```bash
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_domain_publication_20261106 \
  tests.test_autokom_project_lifecycle_20261106 \
  tests.test_autokom_runtime_broker
cd platform/frontend && npm ci --ignore-scripts && npm test && npm run build
cd ../..
git diff --check && git status --short
```

La suite Python complète comportait 11 échecs préexistants dans l'environnement d'analyse. Ces cas doivent toujours être assainis séparément avant une certification générale de production.

## Mise à jour Ubuntu (une fois le code poussé)

L'ordre évite de lancer le nouveau timer avant les migrations Django :

```bash
sudo autokom update
sudo autokom app deploy --check && sudo autokom app deploy
sudo autokom runtime setup --check && sudo autokom runtime setup --apply
sudo autokom app frontend deploy --check && sudo autokom app frontend deploy --apply
sudo autokom runtime doctor
sudo systemctl is-enabled autokom-domain-reconciler.timer
sudo systemctl status autokom-domain-reconciler.timer --no-pager
```

Le timer active ensuite les domaines existants **sans nouveau déploiement Jenkins**. Pour demander immédiatement un essai de rattrapage, l'administrateur peut, de manière facultative, exécuter `sudo systemctl start autokom-domain-reconciler.service` : ceci **n'est pas une correction manuelle du DNS**, mais le déclenchement du même automate.

### Diagnostics sûrs

```bash
sudo journalctl -u autokom-domain-reconciler.service --since '-30 min' --no-pager -o cat
sudo journalctl -u autokom-platform-broker.service --since '-30 min' --no-pager -o cat | grep -E 'AUTOKOM_[A-Z0-9_]+'
sudo nginx -t
sudo ls /etc/nginx/sites-enabled/ | grep -F 'autokom-customer-portfolio.auto.ikom.mg.conf'
dig +short portfolio.auto.ikom.mg A
curl -I --connect-timeout 8 https://portfolio.auto.ikom.mg/
```

Si un diagnostic est `AUTOKOM_CF_AUTH_DENIED`, réviser le périmètre du jeton Cloudflare **dans le coffre des secrets**, jamais dans Git ni dans la conversation. Si `AUTOKOM_DOMAIN_SUFFIX_MISMATCH` apparaît, vérifier la cohérence de l'option de domaine géré entre le backend Django et le broker. Si `AUTOKOM_ACME_FAILED` apparaît, contrôler DNS public et HTTP-01 depuis Internet. Le timer répétera ensuite les opérations une fois la configuration rétablie.

## Interface

- **Mes projets** : statut coloré, lien HTTPS lorsque confirmé, engrenage tout à droite. Aucun bouton de suppression dans la liste.
- **Détails du projet / publications / sites** : adresse publique cliquable lorsque activée, sinon préparation ou code de diagnostic.
- **Paramètres du projet** : formulaire de modification, archivage/restauration, et **Zone de danger** en bas avec suppression après saisie du nom exact. La suppression conserve l'historique d'audit et n'efface pas de domaines appartenant à d'autres projets.

## Limite de validation

Tests hors production et simulations Cloudflare uniquement. La création du véritable DNS, le certificat, la résolution depuis l'extérieur et le retour HTTP de l'application doivent être validés sur l'instance Ubuntu ; ils ne peuvent pas être garantis par un test local ni par la présence d'un conteneur Docker en état RUNNING.
