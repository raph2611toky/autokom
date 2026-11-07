# AUTOKOM — Administrateur Jenkins choisi au terminal (migration GCP)

## Objectif

AUTOKOM **ne génère plus par défaut les identifiants du premier administrateur** lors d'une première installation Jenkins interactive : l'administrateur renseigne d'abord son identifiant, son nom complet, son e-mail et son mot de passe (masqué, avec confirmation), puis AUTOKOM s'occupe de créer le compte, configurer Jenkins, obtenir un token API interne, installer les plugins, appliquer Matrix Authorization et poursuivre vers Prometheus / Grafana. Aucun clic sur le wizard Web n'est nécessaire pour un Jenkins encore vierge.

Le plugin Mailer (explicitement inclus dans le profil de base) permet d'appliquer l'e-mail de l'administrateur **après** activation des plugins. Le compte de collecte Prometheus reste distinct et dispose seulement des droits Metrics.View.

## Reprise sur GCP

1. Si l'ancienne commande est en attente sur le spinner `Initialisation automatique du premier administrateur Jenkins`, l'interrompre avec Ctrl+C. Cela n'arrête pas directement le service Jenkins.
2. **Avant la mise à jour, vérifier sans exposer les secrets** :

   ```bash
   sudo systemctl is-active jenkins
   sudo journalctl -u jenkins -n 80 --no-pager | grep -Ei 'AUTOKOM_FIRSTBOOT|SEVERE|Exception|error' | tail -25
   sudo test -f /etc/autokom/secrets/jenkins-admin.auth && echo 'Jeton AUTOKOM existant' || echo 'Jeton AUTOKOM absent'
   sudo test -f /var/lib/jenkins/jenkins.install.UpgradeWizard.state && echo 'Setup Jenkins terminé' || echo 'Setup Jenkins non confirmé'
   ```

   Ne **pas** afficher le contenu de `/etc/autokom/secrets/jenkins-admin.auth`, du mot de passe ni des fichiers de staging.

3. Publier le patch ZIP différentiel depuis Kali et mettre à jour le dépôt privé comme d'habitude ; sur GCP :

   ```bash
   sudo autokom update
   sudo autokom install
   ```

4. Pour un Jenkins toujours vierge et reconnu comme tel, AUTOKOM demande :

   ```text
   Identifiant Jenkins :
   Nom complet du responsable Jenkins :
   E-mail de l'administrateur Jenkins :
   Mot de passe Jenkins (masqué) :
   Confirmer le mot de passe Jenkins (masqué) :
   ```

   Les choix sont protégés en root:0600 dans `/etc/autokom/secrets/jenkins-first-admin.profile.json` et `/etc/autokom/secrets/jenkins-first-admin.password`. Le nom et l'e-mail sont dans le premier fichier, le mot de passe **uniquement** dans le second. Aucun secret dans les paramètres du processus, les journaux, le dépôt Git ou l'interface Web.

5. Si une tentative précédente a déjà créé `autokom-admin`, **ce compte est conservé** : AUTOKOM ne doit pas réinitialiser un Jenkins déjà configuré ou ambigu. Dans ce cas, il faut vérifier l'état du service et ajouter le compte personnalisé de manière autorisée après la phase d'initialisation ; ne pas supprimer les comptes ni effacer `/var/lib/jenkins`.

## Sécurité et reprise

- Ancien mot de passe AUTOKOM généré mais **non activé** : conservé en root:0600 sous `jenkins-first-admin.password.previous-auto` lors du nouveau choix explicite, sans effacer un utilisateur Jenkins existant.
- Secret d'un utilisateur Jenkins déjà créé ou jeton en attente : aucune substitution d'identité.
- Configuration Jenkins déjà sécurisée : l'authentification existante et les droits existants sont conservés ; ne jamais forcer un nouveau premier administrateur.
- Diagnostic de démarrage : au lieu d'attendre le marqueur d'installation uniquement, AUTOKOM vérifie la configuration de sécurité et une réponse authentifiée de l'API locale. Les erreurs de hook `AUTOKOM_FIRSTBOOT:` du redémarrage courant déclenchent un message précis sans attendre la fin du délai.
- Plugins : installation automatique ; redémarrage contrôlé ; vérification de Matrix Authorization avant de déclarer la phase terminée.
- Profil Jenkins : mémorisation du profil DevSecOps et du choix du webhook dans `/var/lib/autokom/install/jenkins-plugins-selection.json`, root:0600. Nouvelle exécution de `autokom install` : réutilisation sans menu ; `sudo autokom configure plugins` : modification volontaire des choix. La confirmation de redémarrage est à nouveau requise pour chaque nouvelle exécution qui l’exige.
- Grafana : conserve sa saisie interactive de l'identifiant et du mot de passe en terminal pour une installation neuve ; AUTOKOM utilise ensuite l'API locale pour configurer l'administrateur.

## Vérifications

```bash
sudo autokom status
sudo systemctl is-active jenkins
sudo test -s /etc/autokom/secrets/jenkins-admin.auth && echo 'Identifiants automation présents'
sudo test -f /etc/autokom/secrets/jenkins-first-admin.profile.json && echo 'Compte choisi sauvegardé'
```

**Limite de validation :** tests unitaires et suites Python exécutés localement. L'exécution réelle de Jenkins/Groovy et les appels réseau GCP doivent être confirmés sur le serveur par les journaux et les contrôles ci-dessus.
