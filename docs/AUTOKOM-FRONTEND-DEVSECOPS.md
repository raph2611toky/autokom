# AUTOKOM — Frontend React/Vite et authentification par cookies (novembre 2026)

**État : patch à intégrer après le commit `c35fd0a` sur Kali. Ne rien appliquer sur AWS avant revue.** Le visuel du frontend statique reste inchangé : `src/styles/**`, les illustrations et les composants métier du pipeline ne sont pas remplacés. Les nouveaux écrans de validation de compte / récupération de mot de passe utilisent `AuthFrame` et les classes CSS existantes.

## 1. Arborescence AUTOKOM (pas de /var/www)

```text
/opt/autokom/source/                 source du logiciel et de la CLI existante
/opt/autokom/frontend/
  releases/                          builds Vite versionnés, sans code source
  current -> releases/<ID_RELEASE>    lien atomique publié par Nginx
/var/lib/autokom/frontend/builds/    compilations éphémères non privilégiées
/var/lib/autokom/frontend/npm-cache/ cache npm du builder
/etc/autokom/secrets/frontend.env     options publiques du build (fichier root 0600)
/etc/autokom/secrets/backend.env      secrets Django inchangés (root 0600)
```

Nginx `root /opt/autokom/frontend/current;`, `location /api/` relaie vers Gunicorn `127.0.0.1:<port>` avec `Host: backend.auto.ik0m.tech`, `X-Forwarded-Proto` et cookies HTTP ordinaires (pas de réécriture de cookie Domain/Path). Le site React est exposé en HTTPS sous `auto.ik0m.tech`. Les cookies sans `Domain` appartiennent au domaine du navigateur qui reçoit la réponse proxifiée ; **ne pas faire appeler directement `backend.auto.ik0m.tech` depuis React**.

Le setup refuse les configurations Nginx étrangères et les anciens vhosts HTTPS contenant une racine différente. **Ne jamais remplacer un vhost existant sans vérification de `nginx -T`.** Si un site a déjà été déployé sous `/var/www/autokom/frontend`, migrer son vhost et ses releases dans une fenêtre de maintenance, avec sauvegarde et rollback, sans écraser les fichiers.

## 2. Sessions navigateur : contrat réel Django

| Méthode | Endpoint vu par React | But | Secrets dans la réponse JSON ? |
|---|---|---|---|
| GET | `/api/auth/browser/csrf/` | Délivrer le cookie `csrftoken` | Non |
| POST | `/api/auth/browser/login/` | Identifiants, émission des cookies | Non |
| GET | `/api/auth/me/` | Restaurer la session et le profil | Non |
| POST | `/api/auth/browser/refresh/` | Rotation du refresh en cookie | Non |
| POST | `/api/auth/browser/logout/` | Révoquer session active + supprimer cookies | Non |
| POST | `/api/auth/logout-all/` | Révoquer toutes les sessions | Non |
| POST | `/api/auth/register/` | Créer le compte | Non |
| POST | `/api/auth/email/verify/` | Activer le compte après e-mail | Non |
| POST | `/api/auth/password/forgot/` | Demande non énumérante de lien de récupération | Non |
| POST | `/api/auth/password/reset/` | Changer mot de passe depuis lien signé | Non |

Deux cookies de session sont posés **par Django** : `autokom_access` (`Path=/api/`, 15 min par défaut) et `autokom_refresh` (`Path=/api/auth/browser/`, 7 jours par défaut). Attributs : `HttpOnly`, `Secure` en production, `SameSite=Lax`, aucun `Domain`. Le cookie `csrftoken` est distinct, `Secure` en production, sans `HttpOnly` pour autoriser `X-CSRFToken` dans les requêtes de modification. **JavaScript ne lit aucun JWT.**

Le client `src/services/api.js` applique `credentials: 'same-origin'`, le `X-CSRFToken`, les erreurs API, un seul renouvellement pour 401 et un mécanisme de coordination des refresh entre onglets via Web Locks lorsqu'il est disponible. Sur navigateur sans Web Locks, seule la synchronisation des requêtes d'un même onglet est garantie : tester les sessions multi-onglets avant ouverture au public.

La couche Django `ClientCookieJWTAuthentication` accepte les cookies sur tous les endpoints client qui demandent une authentification et applique CSRF **même aux écritures**. Les endpoints `/browser/login/`, `/browser/refresh/`, `/browser/logout/`, `/browser/github/exchange/` exigent le CSRF même anonymes. Les endpoints JWT Bearer historiques sont conservés : `/api/auth/login/`, `/api/auth/token/refresh/` et `Authorization: Bearer` continuent de fonctionner, sans définir de cookies.

### Configuration backend (déjà en place, à vérifier)

```text
DEBUG=False
ALLOWED_HOSTS=backend.auto.ik0m.tech,127.0.0.1,localhost,...
FRONTEND_URL=https://auto.ik0m.tech
CSRF_TRUSTED_ORIGINS=https://auto.ik0m.tech,https://backend.auto.ik0m.tech,...
TRUST_PROXY_SSL_HEADER=true
SECURE_SSL_REDIRECT=true
```

Les clés JWT et Django restent exclusivement dans `backend.env`. Ne pas mettre `CLIENT_JWT_SIGNING_KEY`, `DJANGO_SECRET_KEY`, `GITHUB_APP_CLIENT_SECRET`, la clé Jenkins ou Cloudflare dans le frontend.

## 3. Mode démonstration et mode API

| Variable `/etc/autokom/secrets/frontend.env` | Production | Fonction |
|---|---|---|
| `FRONTEND_URL` | `https://auto.ik0m.tech` | Domaine public React |
| `BACKEND_URL` | `https://backend.auto.ik0m.tech` | Hôte logique Django (proxy loopback) |
| `VITE_AUTOKOM_API_BASE` | `/api` | Chemins relatifs uniquement |
| `VITE_AUTOKOM_FRONTEND_ONLY` | `false` | `true` conserve la présentation statique |
| `VITE_AUTOKOM_API_ENABLED` | `true` | `false` désactive le parcours réel |
| `VITE_AUTOKOM_AUTH_MODE` | `cookie` | `cookie` recommandé ; `bearer` pour compatibilité |
| `VITE_AUTOKOM_GITHUB_ENABLED` | `false` | Le sélecteur GitHub actuel reste de démonstration |

Le mode **frontend-only** conserve le login fictif, les brouillons locaux, le simulateur de pipeline, les modes DevOps et DevSecOps et le thème. En mode **API**, Login/Register appellent Django ; le profil est chargé au rechargement, les pages projets/déploiement nécessitent une session. **Les dépôts GitHub simulés et le parcours de pipeline statique ne déclenchent toujours pas de déploiement réel.** Le branchement métier à GitHub App et aux déploiements doit être fait séparément, écran par écran, sans confondre maquette et production.

Dossiers/API ajoutés sans modifier les CSS existants : `src/services/api.js`, `authApi.js`, `projectsApi.js`, `deploymentsApi.js` et `src/context/AuthContext.jsx`. Les nouvelles pages utilisent `AuthFrame` sans nouveau système de styles.

Le module hérité `src/config/projectAnalysisApi.js` contient des routes fantômes non implémentées dans Django et n'est pas appelé par l'interface actuelle. Ne pas l'utiliser pour lancer un vrai déploiement ; la liaison effective devra utiliser les APIs projet/environnement/déploiement existantes (création puis `GET deployments/preview/`, puis `POST deployments/launch/` avec `Idempotency-Key`). Le fichier `supabaseClient.js` est lui aussi hérité, non importé par l'application actuelle ; il ne doit pas être réintroduit comme mécanisme d'auth concurrent.

## 4. Préparation locale et tests AVANT le serveur

```bash
cd ~/SOUTENANCE/AUTOKOM
git status --short
# À partir du commit c35fd0a et d'un worktree propre :
git apply --check ~/Downloads/AUTOKOM_Frontend_Cookies_Opt_2026-11-04.patch
git apply ~/Downloads/AUTOKOM_Frontend_Cookies_Opt_2026-11-04.patch
npm --prefix platform/frontend test
PYTHONPATH=scripts/lib python3 -m unittest discover -s tests -p test_autokom_frontend_manager.py -v
python3 -m compileall -q platform/backend/apps/accounts/user scripts/lib
npm --prefix platform/frontend ci --ignore-scripts
npm --prefix platform/frontend run build
# Django test DB isolée, avec dépendances + MySQL de test configurés :
cd platform/backend && python manage.py test apps.accounts.user.test_browser_auth
```

Ne pas activer ce mode tant que les tests Django avec CSRF `enforce_csrf_checks` et les tests d'intégration HTTPS ne passent pas. Le pipeline Jenkins backend actuel exécute déjà `python manage.py test` avec une base MySQL dédiée ; le pipeline frontend inclut le mode cookie dans le build, tests Node, npm audit, Semgrep, Trivy.

## 5. Après revue et seulement ensuite sur AWS

```bash
sudo autokom update
sudo autokom app frontend env show
sudo autokom app frontend setup \
  --domain auto.ik0m.tech \
  --backend-domain backend.auto.ik0m.tech \
  --nginx-mode managed --tls-mode certbot \
  --email VOTRE_EMAIL --check
# Après contrôle Nginx, DNS, Certbot et sauvegarde :
sudo autokom app frontend setup --domain auto.ik0m.tech \
  --backend-domain backend.auto.ik0m.tech --nginx-mode managed \
  --tls-mode certbot --email VOTRE_EMAIL --apply
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Si le vhost `auto.ik0m.tech` est déjà géré manuellement, utiliser `--nginx-mode existing --tls-mode existing` après revue manuelle du vhost et des permissions `/opt/autokom/frontend`. L'option `existing` ne modifie pas le vhost.

### Smoke tests après publication

```bash
curl -I https://auto.ik0m.tech/
curl -i https://auto.ik0m.tech/api/health/
curl -i -c /tmp/autokom-csrf.cookies https://auto.ik0m.tech/api/auth/browser/csrf/
sudo nginx -t
sudo autokom app frontend status
```

Vérifier via DevTools que le CSRF apparaît et que **les JWT sont uniquement dans les cookies HttpOnly**, jamais dans `Local Storage`, `Session Storage`, les réponses JSON de connexion ou les URL. Exercer inscription, lien e-mail, connexion, rechargement, expiration access/refresh, changement de mot de passe, déconnexion, navigation directe `/projects`, rollback.

**Limites de ce patch :** aucun déploiement AWS réalisé ; le cycle frontend React n'a pas été raccordé aux vrais dépôts GitHub ni à l'action Jenkins. Les tests Django dépendent de l'environnement Python/MySQL et doivent être validés en CI avant mise en production. Les tests Node de logique et les tests de CLI peuvent s'exécuter sans AWS.
