# Libérer les domaines après suppression d’un projet AUTOKOM

**Avant mise en production** — appliquer aussi le correctif précédent
`AUTOKOM-RETRAIT-HA-SOUSCRIPTION.zip` si les workers GCP sont utilisés. Il retire
les réplicas et les manifestes associés au projet supprimé.

Depuis une copie à jour du dépôt, extraire ce ZIP à la racine, examiner les
modifications, les commiter et pousser la branche. Puis, sur le serveur :

```bash
cd /opt/autokom/source
sudo autokom update &&
sudo autokom devsecops apply --deploy --auto-notify
```

Ce déploiement recharge Django, le broker, le worker de déploiement et le
frontend. Si le correctif HA précédent vient d’être appliqué, vérifiez que le
timer est actif. Utilisez `sudo autokom iac ha delivery --apply` si le timer
ou la mise à jour des workers doivent encore être installés.

## Portfoliolady déjà supprimé

```bash
sudo autokom app backend domains release-deleted --hostname portfoliolady.auto.ikom.mg
sudo autokom app backend domains release-deleted --hostname portfoliolady.auto.ikom.mg --apply
sudo autokom app backend domains release-deleted --hostname portfoliolady.auto.ikom.mg
```

La première commande liste le projet supprimé et toutes ses réservations
avant de les libérer avec `--apply`. La dernière doit afficher
« aucune réservation en base ». Sur GCP, `sudo autokom iac ha apps` doit
cesser d’afficher le projet après la réconciliation des workers si le
correctif HA précédent est actif.

Si le broker indique que l’ancien enregistrement DNS n’est pas identifiable
comme géré par AUTOKOM, vérifiez précisément le nom, son ancienne affectation
et la propriété de l’enregistrement dans Cloudflare. Retirez seulement
l’enregistrement résiduel confirmé comme appartenant à ce projet, puis relancez
`--apply`. Ne modifiez pas le wildcard ni un enregistrement utilisé ailleurs.
Consultez `sudo journalctl -u autokom-platform-broker.service -n 50 --no-pager`
pour le code détaillé si une étape échoue. Lorsque le contrôleur GCP est
occupé, attendez la fin du cycle et réessayez.

## Ce qui change

- La suppression retire les routes Nginx et les enregistrements DNS gérés
  dont la propriété est confirmée, ainsi que la route GCP de l’environnement.
  La réservation est libérée en base uniquement après confirmation du retrait.
- Un domaine personnalisé est libéré dans AUTOKOM. Son DNS externe n’est pas
  modifié. Les certificats, l’historique des publications et les règles
  d’éligibilité par dépôt GitHub restent en place.
- L’identifiant et le nom affiché du projet historique sont conservés. Son
  identifiant d’URL interne change pour que le même nom soit à nouveau
  disponible dans l’organisation.
- Un échec laisse la réservation en base. Si le retrait est partiellement
  effectué, la suppression peut être relancée sans recréer les ressources.
  Un projet actif ou simplement archivé n’est jamais modifié par
  `release-deleted`.
