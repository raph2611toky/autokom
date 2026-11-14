# AUTOKOM · Réplicas retirés et souscription de compte

Ce correctif est basé sur l'archive de la version actuellement utilisée. Il ne modifie ni le MIG, ni Terraform, ni la méthode de suppression des enregistrements Cloudflare. Les VM peuvent héberger plusieurs applications. Seuls la route GCP et les conteneurs de l'application supprimée sont retirés. L'historique et les images restent conservés.

## Mise à jour

Extraire les fichiers à la racine du dépôt AUTOKOM, vérifier `git diff`, committer et pousser sur `main`. Une fois les builds en cours terminés, lancer sur la VM.

```bash
sudo autokom update &&
sudo autokom app deploy &&
sudo autokom app frontend deploy --apply
```

`autokom update` synchronise aussi le code des workers si le timer de synchronisation actif est déjà installé. Vérifier la sortie de la mise à jour. Il n'y a pas de migration de schéma dans cette archive.

## Vérifier le retrait de portfoliolady

```bash
sudo systemctl start autokom-ha-delivery.service
sudo autokom iac ha delivery --status
sudo autokom iac ha apps
```

Après réconciliation des workers, `portfoliolady.auto.ikom.mg` doit disparaître de l'inventaire, ainsi que ses deux réplicas. Les applications `cv-admin-dashboard-lady` et `myportfolio` doivent rester présentes si elles sont toujours déployées. Un worker MIG ne doit pas être supprimé parce qu'il hébergeait un réplica. Si le nettoyage reste en attente, consulter `sudo journalctl -u autokom-ha-delivery.service -n 100 --no-pager`. Le DNS résiduel éventuel ne prouve pas qu'une route ou un conteneur est encore présent.

## Vérifier la souscription

```bash
sudo autokom app --mode-status
```

Avec un compte AUTOKOM actif et vérifié dont l'adresse est sur la liste autorisée, ouvrir **Tarifs**, sélectionner une périodicité, cliquer sur **Essentiel** ou **Professionnel**, puis confirmer. L'écran précise le débit de **0 Ar** et ne demande ni carte ni autre moyen de paiement. **Mon compte** affiche ensuite la formule enregistrée par le serveur. **Déployer** lit l'abonnement du compte, sans permettre de le modifier. Vérifier également qu'un compte non autorisé reste sur l'offre gratuite de lancement et que l'accès direct à la page de souscription ne permet aucune activation.
