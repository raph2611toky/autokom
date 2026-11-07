# AUTOKOM — reprise de l'initialisation Jenkins sur GCP

## Défaut corrigé

Après `sudo autokom configure services`, l'ancien `scripts/lib/services.py` créait
`/var/lib/autokom/install` avec le mode `0755`, puis
`jenkins_postinstall.py` refusait de sauvegarder le profil de plugins si le
répertoire n'était pas `0700` : `Répertoire d’installation AUTOKOM non privé`.

Les deux assistants utilisent désormais `private_install_directory()`.
Un dossier d'installation appartenant à root, sans écriture groupe/autres,
est ramené de `0755`/`0750` vers `0700` de façon idempotente. Aucun fichier
n'est supprimé. Les liens symboliques, un propriétaire étranger et les dossiers
accessibles en écriture à d'autres utilisateurs restent refusés.

## Comptes Jenkins lors de tentatives déjà interrompues

Un ancien correctif pouvait avoir créé le compte technique `autokom-admin` sans
achever le jeton d'API et la fin du wizard. Le nouveau code conserve ce compte
et autorise l'initialisation du compte **choisi dans le terminal** seulement
après preuve de l'ancienne identité : `HudsonPrivateSecurityRealm.Details.isPasswordCorrect`
compare le mot de passe historique stocké localement sous root.

Conditions strictes :

- Jenkins est encore au premier démarrage ; configuration locale reconnue.
- L'init hook existant est exactement l'un des deux hooks AUTOKOM publiés
  (empreintes SHA-256 épinglées). Son ancienne version est sauvegardée sous
  `/etc/autokom/secrets/` puis remplacée par le hook mis à jour.
- Le seul autre compte local effectivement capable de s'authentifier est
  `autokom-admin`. Il est prouvé par son mot de passe AUTOKOM précédent.
- Le compte administrateur choisi est distinct. Ni ce compte précédent ni les
  autres utilisateurs ne sont supprimés / réinitialisés.
- Après installation du plugin Matrix Authorization, les comptes
  précédemment prouvés et nouvellement créés conservent l'administration.
- Si des utilisateurs non identifiés sont présents, l'opération s'arrête
  avant de leur modifier leurs permissions ou leur mot de passe.

Le mot de passe n'est ni affiché ni logué ni passé comme argument de processus ;
il n'est déposé que dans un fichier de préparation local privé pour Jenkins.
Ce fichier transitoire est supprimé après réussite du bootstrap.

## Application

Depuis Kali : appliquer le ZIP différentiel dans le clone AUTOKOM,
lancer les tests, puis `git add` les fichiers du patch, `git commit`, `git push`.
Sur GCP : `sudo autokom update`, puis `sudo autokom install`.
Le profil plugins ne doit plus bloquer sur les permissions du répertoire.
L'administrateur Jenkins est demandé une seule fois et conservé en root:0600.

## Vérification sans exposer les secrets

```sh
sudo stat -c '%U:%G %a %n' /var/lib/autokom/install
sudo systemctl is-active jenkins
sudo test -s /etc/autokom/secrets/jenkins-admin.auth && echo 'Jeton vérifié' || echo 'Jeton absent'
sudo journalctl -u jenkins --since '10 minutes ago' --no-pager | grep -E 'AUTOKOM_FIRSTBOOT|AUTOKOM_MATRIX|SEVERE|Exception' | tail -30
```

Les tests Python vérifient les comportements filesystem et les préconditions
statiques des scripts Groovy. Ils ne démontrent **pas** la réussite d'un
redémarrage Jenkins réel sur GCP : ce contrôle se fait sur l'instance.

## Si le garde-fou « other configured accounts » apparaît

Ne supprimer ni `/var/lib/jenkins`, ni `/var/lib/jenkins/users`, ni les fichiers
d'identité. Vérifier avec l'administrateur quels comptes locaux sont présents.
AUTOKOM refuse à dessein de transformer un contrôleur comportant des comptes
non reconnus par ce chemin de première installation.
