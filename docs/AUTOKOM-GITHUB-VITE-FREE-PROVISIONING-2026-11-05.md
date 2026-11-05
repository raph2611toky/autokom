# AUTOKOM — Parcours GitHub, détection de projets et offre Vite gratuite

Ce correctif part de l'archive `autokom(1).zip` plus le patch OAuth `2fa5f8c` appliqué dans la conversation.
Il **ne modifie pas les composants de design ni les modes de démonstration**. Les fichiers CSS existants sont conservés sauf un style accent orange pour rendre l'action GitHub lisible.

## Fonctionnement cible

1. **Compte AUTOKOM** : les boutons « S'inscrire avec GitHub » et « Se connecter avec GitHub » utilisent déjà le callback `/api/auth/github/callback/` et les cookies HttpOnly. Une adresse e-mail locale préexistante doit être associée explicitement : pas de fusion par e-mail.
2. **Autorisation d'accès aux dépôts** : après inscription GitHub réussie, le frontend démarre automatiquement une autorisation GitHub de lecture des dépôts. La demande d'autorisation de GitHub demeure obligatoire.
3. **GitHub App non installée** : après OAuth, AUTOKOM ouvre l'URL officielle d'installation si aucune installation n'est présente, au plus une fois par parcours. Il n'est pas possible d'accorder l'installation sans l'accord de l'utilisateur dans GitHub.
4. **Retour de GitHub** : les installations listées par Django/GitHub sont la seule source d'autorité. Une installation unique est présélectionnée ; si plusieurs sont disponibles, AUTOKOM privilégie une installation nouvellement autorisée ou un `installation_id` retourné par GitHub **uniquement s'il apparaît dans la liste autorisée**. Si l'ambiguïté demeure, le sélecteur reste disponible.
5. **Dossier du projet** : il désigne les **sources** dans le dépôt, non l'artefact compilé. Pour `portfolio/portfolio-app`, sélectionner `portfolio-app` si ce sous-dossier contient `package.json` et `package-lock.json`.
6. **Détection réelle** : le backend renvoie seulement les noms de fichiers et les noms de dépendances/présence des scripts depuis `package.json` (maximum 64 Ko). Aucun fichier source brut ni secret GitHub n'est renvoyé à cette API d'analyse.
7. **Profil gratuit** : Django provisionne idempotemment son plan DevOps gratuit lors de `autokom app deploy`, après les migrations. Seul `STATIC` pour React/Vue/Svelte + Vite et `package-lock.json`, avec `dist` sur le port 8080, est autorisé au plan gratuit. Les modes Dockerfile backend, Next.js SSR, PHP/Laravel, Python/Django/Flask, Java et Node serveur restent sous droits serveur/payants.
8. **Build Vite** : le runner BuildKit génère à l'exécution un Dockerfile AUTOKOM fixe : `node:22-alpine` + `npm ci --ignore-scripts` + `npm run build` + serveur `nginx-unprivileged` sur 8080. La commande npm du dépôt s'exécute toujours dans le builder isolé, sans droits root ni socket Docker, comme pour le parcours Dockerfile précédent.
9. **DevSecOps** : pas de lancement Jenkins avant prévisualisation, validation du plan, confirmation explicite et idempotence. La variante DevSecOps reste payante et n'est pas activée artificiellement.

## Réglage GitHub App requis (une seule fois, chez GitHub)

Sous la GitHub App `autokom-deploy`, configurer :

- Callback comptes : `https://backend.auto.ikom.mg/api/auth/github/callback/`
- Callback dépôts : `https://backend.auto.ikom.mg/api/projects/git/github/callback/`
- URL de configuration après installation (*Setup URL*) : `https://auto.ikom.mg/deploy`
- Désactiver **Request user authorization (OAuth) during installation** si l'on conserve la Setup URL et les deux OAuth distincts. Cette option GitHub redirige vers le *premier* callback au lieu de la Setup URL ; elle peut brouiller le state de deux flux AUTOKOM.
- Vérifier l'accès aux dépôts et les événements webhook nécessaires. Ne demander que les permissions réellement utilisées.

Les préférences et permissions GitHub App ne peuvent pas être modifiées en toute sécurité par AUTOKOM sans intervention du propriétaire de l'application. AUTOKOM ne peut pas installer l'App **sans le consentement GitHub**.

## Kali (sur la version qui inclut `2fa5f8c`)

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline
git apply --check /home/raph/Downloads/AUTOKOM_GitHub_Vite_Free_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_GitHub_Vite_Free_2026-11-05.patch
npm --prefix platform/frontend test
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_free_static_build
python3 -m compileall -q platform/backend/apps/projects scripts/runtime/autokom_agent_local.py scripts/lib/autokom_app_manager.py
git diff --check
git add docs platform/frontend platform/backend/apps/projects scripts/lib/autokom_app_manager.py scripts/runtime/autokom_agent_local.py tests/test_autokom_free_static_build.py
git commit -m 'feat: streamline GitHub installation and enable free Vite static deploys'
git push origin main
```

## Ubuntu AWS (après push seulement)

**Aucune nouvelle installation manuelle Node.js, npm, Nginx ou Certbot.**

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app status
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Tester ensuite, dans cet ordre : inscription GitHub sur **un compte de test nouveau**, retour vers AUTOKOM, installation GitHub App, présélection du compte, sélection d'un dépôt Vite **avec `package-lock.json`**, choix des sources, offre gratuite, prévisualisation puis lancement Jenkins explicite. Pour un compte possédant déjà une adresse AUTOKOM, tester le flux **Associer au compte** après connexion par e-mail plutôt que créer un doublon.

**Important :** le `--check` backend n'initialise pas le plan gratuit (sans écriture) ; `sudo autokom app deploy` le fait après migrations. Les tests `manage.py test` doivent être exécutés avec une **base MySQL de test isolée**, et l'agent Jenkins doit disposer de BuildKit, du credential GitHub et de sa clé runner. `autokom app frontend deploy --check` ne valide pas à lui seul la construction d'un projet client.

## Tests de contrôle d'intégration (base Django isolée)

```bash
# Dans un environnement de test, avec le Django du projet et une base MySQL de test :
python manage.py test apps.projects.git.test_directories apps.projects.configurations.test_free_static
```

## Limites de cette version

- Cette recette static est **Vite + npm lockfile** et `dist` ; pnpm, Yarn, Angular output personnalisé, Next.js SSR, Dockerfile backend, bases de données clientes et buildpacks ne sont pas automatiquement rendus gratuits.
- Les images `node:22-alpine` et `nginxinc/nginx-unprivileged:1.29-alpine` doivent être verrouillées par **digest immutable** dans la politique de production, après qualification et scan. Le code actuel emploie des tags : c'est une limite de traçabilité de la chaîne d'approvisionnement.
- Aucun paiement ni activation artificielle des offres serveur n'a été ajouté. Les offres payantes sont désactivées côté UI API et vérifiées par l'entitlement Django.
- La réussite locale des tests n'est pas une preuve d'une autorisation GitHub réelle ni d'un premier déploiement BuildKit/Jenkins de client.
