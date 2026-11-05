# AUTOKOM — Réparation de l'isolation du builder frontend (2026-11-05)

## Cause confirmée dans le code

`sudo autokom` démarre avec les droits administrateur, mais `npm`, les tests,
Semgrep, Trivy et Vite s'exécutent sous `autokom-frontend-builder` par `runuser`.
Ce compte est volontairement séparé de `autokom` (Django).

Le backend configure `/var/lib/autokom` comme `autokom:autokom` mode `0750`.
Le patch précédent corrigeait `/var/lib/autokom/frontend` mais **pas ce parent**.
Un compte builder non membre du groupe `autokom` ne peut donc pas accéder à
`builds/.../source/package.json`, même si le fichier appartient au builder.

## Réparation automatique

- Vérifie les droits effectifs par `runuser -u autokom-frontend-builder`.
- Ajoute *uniquement* une ACL nominative de traversée (`--x`) sur
  `/var/lib/autokom` si le droit est absent. Aucun `chmod 755`, aucun ajout du
  builder au groupe `autokom`, aucun accès au code Django ou aux secrets.
- Si le programme `setfacl` est absent, AUTOKOM installe automatiquement le
  paquet `acl` par l'APT signé d'Ubuntu, sans commande manuelle.
- Maintient le répertoire frontend parent en `root:autokom-frontend-builder`
  mode `0710` et les zones de travail privées du builder en `0700`.
- Prépare un `HOME` privé et des caches writable dédiés à npm et Trivy. Les
  variables `HOME`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` et
  `TRIVY_CACHE_DIR` sont cohérentes avec ce compte limité.
- Refuse les répertoires symboliques ou les propriétaires inattendus dans le
  chemin de build.
- Verrouille `deploy --check`, `deploy --apply` et `rollback` pour qu'un seul
  processus puisse publier ou restaurer une release à la fois.
- N'altère aucun fichier React, CSS, Vite, backend, domaine DNS ou vhost Nginx.

## Kali Linux : intégration du patch

Le dernier commit partagé est `f1e7a04` : ne réappliquez pas les correctifs
précédents. Téléchargez le nouveau patch, puis :

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline

git apply --check /home/raph/Downloads/AUTOKOM_Frontend_Builder_ACL_FullPath_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Frontend_Builder_ACL_FullPath_2026-11-05.patch

PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_frontend_manager \
  tests.test_autokom_env_navigation \
  tests.test_autokom_keyboard_menus \
  tests.test_autokom_env_domain_migration

npm --prefix platform/frontend test
git diff --check

git add scripts/lib/autokom_frontend_manager.py \
  tests/test_autokom_frontend_manager.py \
  docs/AUTOKOM-FRONTEND-BUILDER-ACL-ISOLATION.md
git commit -m 'fix: frontend builder ACL and scanner cache isolation'
git push origin main
```

## Ubuntu AWS : reprendre depuis la configuration déjà en place

Le setup sur `https://auto.ikom.mg` est **déjà terminé**. Node/npm, certificat
Let's Encrypt, vhost et DNS ont été installés. Ne lancez pas à nouveau setup
ni Certbot pour cette correction.

```bash
sudo autokom update
sudo autokom app frontend status
sudo autokom app frontend deploy --check
```

La commande `deploy --check` configure automatiquement les ACL et les caches,
puis déclenche les 6 étapes existantes (npm ci, tests, npm audit, Semgrep,
Trivy, Vite). Ne passez à `--apply` que si elle réussit.

```bash
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
sudo nginx -t
readlink -f /opt/autokom/frontend/current
curl -I https://auto.ikom.mg/
curl -i https://auto.ikom.mg/api/health/
```

### Vérification facultative, en lecture seule

```bash
namei -l /var/lib/autokom/frontend/builds
sudo getfacl -p /var/lib/autokom | grep -E '^(# owner:|# group:|user:autokom-frontend-builder:|mask:|other:)'
sudo runuser -u autokom-frontend-builder -- test -x /var/lib/autokom \
  && echo 'Traversée parent autorisée'
sudo ls -lt /var/log/autokom/frontend/ | head
```

Ces commandes ne modifient pas les permissions. Ne pas exécuter de `chmod -R`,
`chown -R`, ajouter le builder au groupe `autokom`, ni installer npm à la main.

## Défaillances suivantes anticipées

1. **Accès au parent partagé** : ACL minimale, vérifiée effectivement sous le builder.
2. **Cache npm** : vérification d'écriture avant `npm ci`.
3. **Semgrep/Trivy** : HOME et caches indépendants, privés et accessibles.
4. **Builds/rollback simultanés** : un seul processus autorisé par verrou kernel.
5. **Absence de release** : état « pas encore publié » attendu ; pas de correction manuelle de Nginx.
6. **Dépendance ou scanner vulnérable** : `npm audit`, Semgrep et Trivy doivent rester bloquants ; consulter le journal protégé si un contrôle échoue.
7. **DNS** : les résultats Kali indiquent `auto.ikom.mg → 56.228.86.160` ; un échec DNS local temporaire n'explique pas le refus de lecture de package.json.

Le patch est vérifié hors ligne et ne vaut pas preuve d'un build réussi sur AWS.
