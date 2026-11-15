# AUTOKOM · Vérifier le sous-domaine au choix du nom

Sur la console de production, après le choix du dépôt GitHub, le champ **Nom du
projet** interroge Django après une courte pause. Django vérifie la réservation
en base, l’identifiant interne unique dans l’organisation et la disponibilité
DNS et Nginx auprès du broker. Le nom simple est conservé s’il est libre. Sinon,
par exemple, `Portfolio` garde son nom affiché et reçoit une adresse telle que
`portfolio-a1b2c3d4.auto.ikom.mg`. Le récapitulatif affiche la proposition.

La prévisualisation n’écrit ni dans Cloudflare ni dans la base. La création
refait la vérification et réserve la valeur autorisée par le serveur. Deux
créations concurrentes n’écrasent pas une réservation existante. Les domaines
et projets existants ne sont pas renommés. Les domaines personnalisés gardent
leur parcours. La console de préproduction garde sa liste d’hôtes préattribués.
Le plugin DNS et le broker de production existants sont utilisés sans changer
leurs permissions ni ouvrir Jenkins.

## Appliquer

À la racine du dépôt, avec un espace de travail propre et à jour, tester puis
appliquer `AUTOKOM-NOM-SOUS-DOMAINE.patch` avec `git apply --check` et
`git apply`. Le patch est ciblé et ne remplace pas les fichiers complets.
Examiner `git diff`, ajouter les changements, commiter et pousser la branche
déployée. Sur le serveur, exécuter ensuite :

```bash
cd /opt/autokom/source
sudo autokom update &&
sudo autokom devsecops apply --deploy --auto-notify
```

Aucune nouvelle migration de base n’est requise par ce correctif.

## Vérifier

1. Dans Déployer, choisir le dépôt GitHub et saisir un nom libre. Le domaine
   proposé doit apparaître sous le champ avant de continuer.
2. Pour un nom déjà réservé par un projet historique ou par Cloudflare, le
   champ doit proposer une adresse avec un suffixe de huit caractères
   hexadécimaux. La réservation existante ne doit pas être modifiée.
3. Vérifier que l’adresse est reprise au récapitulatif et dans le projet créé.
   Ne pas lancer Jenkins pour un simple aperçu du nom.

La réponse d’aperçu n’est pas une réservation. Un changement survenu entre
l’aperçu et l’enregistrement peut produire une nouvelle adresse, et le serveur
retourne celle qui a effectivement été réservée. Une modification DNS externe
après la réservation ne peut pas être empêchée par le formulaire. Si le DNS
n’est pas joignable pendant la vérification, l’interface le signale et permet
de réessayer avant de continuer.
