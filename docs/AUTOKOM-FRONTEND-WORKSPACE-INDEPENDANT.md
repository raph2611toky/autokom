# AUTOKOM — Workspace de compilation frontend indépendant (2026-11-05)

## Problème résolu

Le backend AUTOKOM détient `/var/lib/autokom` avec le mode `0750` et un compte distinct (`autokom`).
Le frontend est compilé par `autokom-frontend-builder`, qui n'appartient volontairement pas au groupe du backend.
L'ancienne implantation sous `/var/lib/autokom/frontend` dépendait d'une ACL additionnelle sur
`/var/lib/autokom`. Sur AWS, le test réel via `runuser` a échoué malgré la présence du marqueur `+`.
Ce marqueur indique la présence d'une ACL, **pas** la permission effective pour le compte builder.

## Correction structurelle

- Workspace indépendant : `/var/lib/autokom-frontend/`.
- `builds/`, `npm-cache/`, `home/`, `trivy-cache/` restent privés (`0700`) au compte builder.
- Parent workspace détenu par `root:autokom-frontend-builder` et mode `0710`.
- **Aucun `chmod`, `chown`, `setfacl` sur `/var/lib/autokom`**, ni ajout du builder au groupe `autokom`.
- Aucun changement aux releases : `/opt/autokom/frontend/releases/` et `current` demeurent inchangés.
- L'ancien workspace n'est pas supprimé ni déplacé automatiquement ; il peut être retiré ultérieurement
  après un déploiement réussi et une vérification qu'aucun autre processus ne l'utilise.
- Tests de séparation avec `runuser` et un vrai utilisateur non privilégié ; aucune simulation de résultat ACL.

## Déploiement

Sur Kali (à partir de `3400a25`) :

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Frontend_Workspace_Isolation_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Frontend_Workspace_Isolation_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_frontend_manager tests.test_autokom_env_navigation tests.test_autokom_keyboard_menus tests.test_autokom_env_domain_migration
npm --prefix platform/frontend test
git diff --check
git add scripts/lib/autokom_frontend_manager.py tests/test_autokom_frontend_manager.py docs/AUTOKOM-FRONTEND-DEVSECOPS.md docs/AUTOKOM-FRONTEND-WORKSPACE-INDEPENDANT.md
git commit -m "fix: isolate frontend builds from backend data root"
git push origin main
```

Sur Ubuntu, **sans relancer** Certbot ni le setup (ils ont déjà réussi) :

```bash
sudo autokom update
sudo autokom app frontend status
sudo autokom app frontend deploy --check
```

Le deploy prépare lui-même le nouvel espace. Si tous les scans, tests et le build réussissent :

```bash
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
readlink -f /opt/autokom/frontend/current
curl -I https://auto.ikom.mg/
```

## Vérifications en lecture seule

```bash
namei -l /var/lib/autokom-frontend/builds
sudo runuser -u autokom-frontend-builder -- test -w /var/lib/autokom-frontend/npm-cache && echo 'Cache npm OK'
sudo runuser -u autokom-frontend-builder -- test -x /var/lib/autokom-frontend/builds && echo 'Builds OK'
sudo nginx -t
```

Les secrets Django restent privés, et les fichiers React/CSS ne sont pas modifiés.

## Cas d'erreur ultérieur

Si `npm ci`, l'audit, Semgrep, Trivy ou Vite échouent, conserver leur blocage DevSecOps.
Le journal de l'étape est créé en mode `0600` dans `/var/log/autokom/frontend/`.
Ne pas contourner les scans et ne pas remplacer l'ancien workspace par un lien symbolique.
