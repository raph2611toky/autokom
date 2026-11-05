# AUTOKOM — Frontend : permissions de build, état initial Nginx et progression

## Diagnostic vérifié (AWS, 5 novembre 2026)

Le frontend a été configuré sur `https://auto.ikom.mg`. DNS, certificat TLS, Nginx et
Node.js/npm sont en place. La première release n'est pas encore publiée.

L'erreur `npm ci` / `EUSAGE` « package-lock.json introuvable » n'est pas liée à
l'absence de lockfile dans Git. Le déploiement copie bien `package-lock.json`.
Le défaut se trouve dans le chemin de travail `/var/lib/autokom/frontend` :
`builds/` et `npm-cache/` appartiennent au compte de build, mais leur parent
peut rester `root:root 0700`, rendant les sous-dossiers inaccessibles au compte
`autokom-frontend-builder`.

Cette cause a été reproduite localement avec `runuser` et un lockfile réel : avant
correction, `npm ci` affiche exactement le même `EUSAGE` et ne peut pas créer ses
journaux ; avec accès traversant au parent, npm lit le lockfile et peut écrire son
cache. La compilation npm complète n'a pas été vérifiée sur AWS.

## Ce que corrige le patch

- Répare automatiquement l'accès au parent **sans autoriser le listing public** :
  `root:autokom-frontend-builder` et mode `0710`. `builds/` et `npm-cache/`
  restent privés en `0700` avec le compte builder comme propriétaire.
- Refuse les liens symboliques sur les répertoires de travail et les propriétaires
  inattendus ; vérifie, avec `runuser` **avant `npm ci`**, la lecture des deux
  manifests, l'écriture de la copie de build et du cache.
- Utilise l'animation AUTOKOM déjà disponible (`tool_banner.Activity`) durant
  provisionnement Node, DNS Cloudflare, propagation DNS, Certbot, les tests,
  l'audit npm, Semgrep, Trivy et Vite.
- Conserve les sorties brutes des opérations dans `/var/log/autokom/frontend/`
  (répertoire `0700`, fichiers `0600`). Les informations potentiellement
  sensibles sont masquées sur la console avec le filtre utilisé par AUTOKOM.
- Rend le setup plus idempotent : si le vhost AUTOKOM référence un certificat
  TLS déjà installé pour le domaine attendu, aucune nouvelle demande Certbot
  n'est envoyée. Le renouvellement demeure assuré par Certbot.
- Si aucun `current/index.html` n'existe, le vhost AUTOKOM répond **503** au
  lieu d'entrer en boucle SPA et d'afficher une 500. Il ne touche pas aux
  routes `/api/`, `/api/internal/`, ni aux autres vhosts Nginx.
- Ne modifie aucun composant React, CSS, page, route ou API.

## Sur Kali : appliquer et publier

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline

git apply --check /home/raph/Downloads/AUTOKOM_Frontend_Builder_Progress_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Frontend_Builder_Progress_2026-11-05.patch

PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_frontend_manager \
  tests.test_autokom_env_navigation \
  tests.test_autokom_keyboard_menus \
  tests.test_autokom_env_domain_migration
npm --prefix platform/frontend test
git diff --check

git add scripts/lib/autokom_frontend_manager.py \
  tests/test_autokom_frontend_manager.py \
  docs/AUTOKOM-FRONTEND-BUILD-PERMISSIONS-PROGRESS.md
git commit -m 'fix: repair frontend build permissions and show progress'
git push origin main
```

Si `git apply --check` échoue, ne pas utiliser `--force` et ne pas réappliquer
le patch à l'aveugle : comparer le commit local avec la base de ce correctif.

## Sur Ubuntu AWS : déployer sans aucune installation manuelle

Le dernier `setup --apply` de l'utilisateur a terminé avec succès : inutile de
réinstaller Node ou de réémettre le certificat.

```bash
sudo autokom update
sudo autokom app frontend status
```

**Optionnel, avant la première release :** réappliquer le setup AUTOKOM déjà
connu pour installer le garde Nginx 503. Le patch préserve le vhost TLS existant
et évite de redemander un certificat déjà présent et conforme :

```bash
sudo autokom app frontend setup \
  --domain auto.ikom.mg \
  --backend-domain backend.auto.ikom.mg \
  --nginx-mode managed --tls-mode certbot --check
sudo autokom app frontend setup \
  --domain auto.ikom.mg \
  --backend-domain backend.auto.ikom.mg \
  --nginx-mode managed --tls-mode certbot --apply
sudo nginx -t
```

**Vérification DevSecOps** :

```bash
sudo autokom app frontend deploy --check
```

Si et seulement si cette commande a réussi :

```bash
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
readlink -f /opt/autokom/frontend/current
curl -I https://auto.ikom.mg/
curl -i https://auto.ikom.mg/api/health/
```

Pour contrôler la résolution DNS, sans modifier la configuration existante :

```bash
getent ahostsv4 auto.ikom.mg
dig +short A auto.ikom.mg @1.1.1.1
dig +short A auto.ikom.mg @8.8.8.8
```

Les résultats DNS de `curl` peuvent être momentanément différents de ceux d'un
résolveur public. Ne pas masquer un problème DNS en ajoutant une entrée
permanente `/etc/hosts` dans AUTOKOM.

### Si un contrôle échoue

Ne pas utiliser `deploy --apply` après un `deploy --check` en échec. Le message
indique le journal AUTOKOM protégé sous `/var/log/autokom/frontend/`. Inspecter
les dernières lignes sans publier le contenu de secrets, cookies ou tokens :

```bash
sudo ls -lt /var/log/autokom/frontend/ | head
sudo autokom app frontend status
sudo nginx -t
```

Aucun changement manuel de permissions, `npm install`, `npm update`, `apt
install`, suppression de Nginx, ni régénération de lockfile n'est nécessaire.

## Validation exécutée hors production

- 67 tests Python ciblés ; 35 tests frontend JavaScript.
- Reproduction du défaut EUSAGE dans un répertoire parent `0700` : confirmée.
- Contrôle de lecture sous un utilisateur non privilégié après parent `0710` : réussi.
- `nginx -t` sur la configuration Nginx générée avec garde de première release : réussi.
- Non validés depuis cet environnement : installation npm en ligne, résultats
  Semgrep/Trivy en production, DNS en continu, redéploiement AWS, HTTPS public.
