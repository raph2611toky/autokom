# AUTOKOM — Réparation persistante de la liaison Django → broker Unix

## Symptôme

`sudo autokom runtime doctor` affiche « Django ne peut pas communiquer avec le broker » malgré trois services `active`.

Avant le redémarrage, le serveur montrait :

```
root:root    0750 /run/autokom
root:autokom 0660 /run/autokom/platform-control.sock
```

Un socket à 0660 ne suffit pas : le compte Django `autokom` doit également avoir le droit `x` sur chacun de ses répertoires parents. Avec `root:root 0750`, cette traversée est refusée.

## Correction

- La configuration `autokom-platform-broker.service` conserve `User=root` et utilise `Group=autokom` pour que `RuntimeDirectory=autokom` soit créé par **systemd** en `root:autokom 0750` à chaque démarrage. L'ancien `ExecStartPre=chgrp` est supprimé : un changement ad hoc après création du répertoire est fragile.
- Ansible déclare également le mode `0750` et le groupe `autokom` de `/run/autokom` sans exposer le socket à d'autres utilisateurs.
- Le broker vérifie à son démarrage que le répertoire n'est pas un lien symbolique, appartient à root, puis applique le groupe `autokom` et le mode `0750`.
- `runtime doctor` vérifie séparément répertoire, socket, accès réel en utilisateur `autokom`, réponse au `ping` et accès au job Jenkins, avec catégories d'échec sans secrets.

**Le code ne modifie aucun vhost, domaine, base de données ni configuration du frontend.**

## Kali (le patch est basé sur le dernier code validé après `62b93fc`)

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline
git apply --check /home/raph/Downloads/AUTOKOM_Runtime_Socket_Group_Fix_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Runtime_Socket_Group_Fix_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_runtime_socket_permissions \
  tests.test_autokom_runtime_broker \
  tests.test_autokom_runtime_deployment_hardening
git diff --check
git add ansible/roles/platform_runtime/tasks/main.yml \
  scripts/systemd/autokom-platform-broker.service \
  scripts/lib/autokom_platform_broker.py \
  scripts/lib/autokom_runtime_admin.py \
  tests/test_autokom_runtime_socket_permissions.py \
  docs/AUTOKOM-RUNTIME-SOCKET-OWNER-GROUP-2026-11-05.md
git commit -m "fix: persist secure broker socket group access"
git push origin main
```

## Ubuntu — ne pas exécuter de déploiement client avant `doctor` vert

1. Vérifier qu'aucune publication client n'est en cours ; `runtime setup --apply` redémarre broker et worker.
2. `sudo autokom update`.
3. `sudo autokom runtime setup --check`.
4. `sudo autokom runtime setup --apply` (confirmation interactive).
5. `sudo autokom runtime doctor`.
6. Si échec, consulter `sudo stat -c '%A %U:%G %n' /run/autokom /run/autokom/platform-control.sock`, `sudo systemctl show autokom-platform-broker -p User -p Group -p RuntimeDirectory -p RuntimeDirectoryMode` et les journaux du broker, **sans secrets**.
7. Une fois le diagnostic vert, vérifier le job avec `sudo autokom runtime jenkins --check` puis retester le déploiement d'un projet de démonstration autorisé.

Le `runtime doctor` a plusieurs niveaux : un broker joignable n'atteste pas encore qu'un agent Jenkins isolé, les credentials Jenkins et BuildKit soient opérationnels.

## Vérifications locales du patch

Tests Python unitaires, test de véritable traversée de socket avec UID non privilégié sur Linux, `systemd-analyze verify`, compilation des sources et application de patch à une extraction propre. Ils ne remplacent pas le test sur AWS avec l'identité `autokom` et l'instance Jenkins réelle.
