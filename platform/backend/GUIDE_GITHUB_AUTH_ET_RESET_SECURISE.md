# AUTOKOM Platform — connexion/inscription GitHub et reset chiffré

## Portée

Ce patch modifie **uniquement `platform/backend/`**. Les parcours existants e-mail/mot de passe, vérification, refresh JWT, logout, Django Admin et GitHub de `projects/git` sont conservés.

## Configuration indispensable de la GitHub App

Depuis les paramètres de la GitHub App AUTOKOM existante :

1. Conserver le callback **projets** déjà installé : `https://backend.auto.ik0m.tech/api/projects/git/github/callback/`.
2. **Ajouter comme second callback** : `https://backend.auto.ik0m.tech/api/auth/github/callback/`.
3. Dans les **User permissions**, autoriser **Email addresses: Read-only**. Vérifier que les utilisateurs acceptent la mise à jour des permissions.
4. Garder l'App client ID et secret déjà présents dans le fichier de secrets du serveur. **Ne pas les coller dans Git**.
5. Facultatif : `GITHUB_AUTH_CALLBACK_URL` permet de fixer explicitement le second callback. Par défaut AUTOKOM le déduit du domaine `GITHUB_APP_CALLBACK_URL`. Aucun changement d'environnement n'est requis si la configuration actuelle est correcte.
6. Facultatif et recommandé : une clé dédiée `TOKEN_ENCRYPTION_KEY` aléatoire (minimum 24 caractères, idéalement 32 octets encodés). Pour compatibilité, AUTOKOM utilise `AES_KEY`, puis `SECRET_KEY`, si la clé dédiée n'est pas fournie. **Ne pas modifier ces clés après émission des liens sans planifier leur invalidation.**

Référence GitHub : https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app

## Endpoints (tous les détails sont dans Swagger)

| Action | Méthode | API |
| --- | --- | --- |
| Début connexion ou inscription | GET | `/api/auth/github/start/?intent=login` ou `signup` |
| Association d'un compte AUTOKOM déjà connecté | POST + Bearer client | `/api/auth/github/link/start/` |
| Callback GitHub (redirection navigateur) | GET | `/api/auth/github/callback/` |
| Échange du ticket OAuth court contre JWT clients | POST | `/api/auth/github/exchange/` |
| Mot de passe oublié (existant, amélioré) | POST | `/api/auth/password/forgot/` |
| Réinitialisation (existant, amélioré) | POST | `/api/auth/password/reset/` |

### Logique côté frontend (à intégrer ensuite)

1. Faire `GET /api/auth/github/start/?intent=login` (ou `signup`). Lire `data.authorization_url` et `data.browser_binding`; **stocker `browser_binding` dans `sessionStorage` avant la redirection**, puis naviguer vers l'URL officielle GitHub. Ne jamais placer le binding dans l'URL GitHub ni en localStorage.
2. GitHub redirige vers le **nouveau** callback backend (ne pas le confondre avec `/api/projects/git/github/callback/`).
3. Le backend redirige vers `https://auto.ik0m.tech/auth/github/callback#status=ok&ticket=...` ; le ticket opaque de **90 secondes**, lié au navigateur initiateur est dans le fragment URL, non envoyé au serveur web par le navigateur.
4. Lire le fragment **une fois**, l'effacer immédiatement avec `history.replaceState(...)`, puis `POST /api/auth/github/exchange/` avec `{ "ticket": "...", "browser_binding": "..." }` (binding lu depuis sessionStorage, effacé immédiatement après l’échange). Cette API retourne la **même paire de JWT clients** que le login classique.
5. Les statuts sans ticket : `linked`, `account_exists`, `already_linked`, `denied`, `error`. En cas de `account_exists`, demander le login AUTOKOM traditionnel puis l'association volontaire GitHub. Ne jamais fusionner automatiquement deux comptes sur l'adresse mail.
6. Les JWT AUTOKOM ne doivent pas être placés dans l'URL. Mettre `Referrer-Policy: no-referrer` sur la page de callback et ne charger aucun script tiers avant nettoyage du fragment.
7. Lier un GitHub à un client ne remplace PAS l'autorisation GitHub de l'espace **Projects**, qui gère séparément les installations et dépôts.

### Mot de passe lors d'une inscription via GitHub

L'e-mail principal **vérifié** est récupéré via `GET /user/emails` (permission *Email addresses: read*), ainsi que le nom GitHub. Un mot de passe aléatoire `secrets.token_urlsafe(64)` est haché avec Django puis jamais envoyé par mail ou affiché. Il est possible de définir ultérieurement un mot de passe local par le parcours sécurisé `forgot/reset`.

### Reset : signé, chiffré, usage unique

`generate_secure_action_token` produit un JWT HS256 (`purpose`, `sub`, `jti`, `iat`, `nbf`, `exp`, `ver`) puis `enc_dec` applique **AES-256-GCM** avec nonce aléatoire et authentification. L'URL ne contient **aucun e-mail ni identifiant en clair**, mais le lien reste un **secret d'accès temporaire** et doit être protégé. Seule l'empreinte SHA-256 est en base `UserActionToken`. Au reset : empreinte + expiration DB + validation cryptographique + version du compte + verrou transactionnel, puis invalidation du jeton et révocation des sessions. Les anciens tokens émis avant le patch ne sont acceptés que tant que leur expiration préexistante en base est valide.

### Installation

Sur Kali, depuis la racine du dépôt :

```bash
unzip -o AUTOKOM_GitHub_Auth_Reset_Secure_PLATFORM_ONLY.zip
cd platform/backend
python -m compileall -q apps/accounts/user helpers/secure_actions.py
python manage.py check
python manage.py makemigrations --check --dry-run
```

Ne lancer les vérifications Django qu'avec le venv et la configuration MySQL existants. Commiter uniquement les fichiers du patch et la migration versionnée. Sur le serveur : `sudo autokom update`, `sudo autokom app deploy --check`, `sudo autokom app deploy`, puis vérifier l'endpoint Swagger et l'API sur HTTPS. Cette migration crée trois tables ; elle ne supprime aucune table existante.

**Important :** ce premier lot couvre uniquement le backend. La page frontend `/auth/github/callback` et les boutons GitHub doivent être raccordés dans une étape distincte.

**Sécurité :** la réinitialisation des mots de passe *administrateurs Django* n'est pas affectée ; seules les identités clientes AUTOKOM sont concernées. Les échanges OAuth en production doivent être testés après configuration du second callback et des permissions GitHub. La protection contre les abus requiert le cache/throttling partagé en environnement multi-workers.
