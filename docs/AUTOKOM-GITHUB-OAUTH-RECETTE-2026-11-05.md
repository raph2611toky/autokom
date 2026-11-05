# AUTOKOM — GitHub OAuth, logs sécurisés et recette du parcours réel

Base du correctif : archive `autokom(1).zip` fournie le 5 novembre 2026. Conserver les composants React, les thèmes et le mode démonstration existants.

## Diagnostic du défaut actuel

- `GET /api/projects/git/github/status/` répond `configured: true`, mais ne teste pas les identifiants auprès de GitHub.
- La redirection HTTP `302` du callback est attendue dans OAuth ; elle ne valide pas, à elle seule, l'obtention du jeton.
- Django renvoyait `status=error` pour toute exception du callback, sans diagnostic applicatif ; React affichait toujours un refus, même pour une erreur technique.
- Le parcours GitHub login/signup disposait de routes Django mais aucun écran React n'effectuait l'échange sécurisé des tickets.
- `autokom app logs` existait ; `autokom app backend logs` n'était pas une sous-commande reconnue.

## Fonctionnement du correctif

- `autokom app backend github doctor` : préflight local, jamais de secret affiché. Ne démontre pas la validité réelle des identifiants chez GitHub.
- `autokom app backend logs --github --lines 80` : uniquement des messages applicatifs GitHub prédéfinis. Les query strings sensibles des access logs Gunicorn en sont exclues.
- L'échec du callback GitHub est journalisé par **étape et catégorie**, sans le code, state, ticket, e-mail ou token.
- `Continuer` au premier écran de déploiement est désactivé tant que l'autorisation et une sélection valide ne sont pas présentes.
- `Se déconnecter` est rouge via `--ak-danger` dans les thèmes existants.
- Connexion/inscription GitHub par callback `/auth/github/callback` dans React : state/PKCE validés par Django, secret navigateur dans `sessionStorage`, ticket à usage unique limité, échange par `/api/auth/browser/github/exchange/`, session créée uniquement via cookies HttpOnly.
- Un compte existant ne peut **jamais** être récupéré sur la seule correspondance d'adresse e-mail : une association explicite depuis la session connectée est nécessaire.
- `Se connecter avec GitHub` ne crée pas silencieusement de compte ; `S'inscrire avec GitHub` est le parcours de création.

## Paramètres GitHub App à vérifier dans GitHub (action du propriétaire de l'application)

Autoriser **deux callbacks** distincts dans la même GitHub App `autokom-deploy` :

1. `https://backend.auto.ikom.mg/api/projects/git/github/callback/`
2. `https://backend.auto.ikom.mg/api/auth/github/callback/`

Vérifier `Metadata: read` et `Contents: read` côté dépôt pour la navigation ; `Email addresses: read` côté compte pour récupérer l'e-mail principal vérifié, si le login/inscription GitHub est souhaité. Installer la GitHub App sur au moins un dépôt autorisé. Les permissions et URLs ne sont **pas** vérifiables par la simple présence de variables `.env`.

Les anciens codes OAuth des captures sont éphémères et ne doivent pas être réutilisés ni divulgués. Ne jamais coller dans une issue une trace brute d'accès au callback contenant `code` et `state`.

## Kali Linux

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Github_OAuth_UX_ExactArchive_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Github_OAuth_UX_ExactArchive_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_github_diagnostic tests.test_autokom_github_enablement
npm --prefix platform/frontend test
python3 -m compileall -q platform/backend/apps/accounts/user platform/backend/apps/projects/git scripts/lib

git diff --check
git status --short
# Après revue du diff et réussite des tests :
git add docs platform/backend/apps/accounts/user platform/backend/apps/projects/git platform/frontend/src platform/frontend/tests scripts/lib/autokom_app_manager.py scripts/lib/autokom_github_doctor.py tests/test_autokom_github_diagnostic.py
git commit -m 'fix: secure GitHub OAuth diagnostics and account login integration'
git push origin main
```

## Ubuntu AWS — après le push uniquement

```bash
sudo autokom update
sudo autokom app backend github doctor
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Aucun setup DNS, Node, Nginx, Certbot ou modification manuelle des variables GitHub n'est nécessaire si la configuration existante est valide.

Pour retrouver le défaut réel **après une seule nouvelle tentative** dans le navigateur :

```bash
sudo autokom app backend logs --github --lines 80
```

- `state` : l'état OAuth est absent, invalide, expiré ou déjà consommé (ou erreur de base de données à cette étape).
- `exchange` : GitHub a refusé l'échange du code, secret/callback incohérents, code expiré, ou problème réseau.
- `identity` : lecture du profil/identité GitHub indisponible.
- `database` : écriture des jetons chiffrés / identité impossible ; vérifier la clé et les données côté Django.

La catégorie affichée donne une direction de diagnostic, **pas une confirmation** de la cause exacte. Si la présence des secrets est validée mais l'échange échoue, vérifier dans GitHub App les deux callbacks et la rotation éventuelle du client secret. Un code OAuth ne peut s'utiliser qu'une seule fois.

## Critères de recette

1. Connecter un compte AUTOKOM existant par e-mail.
2. Dans « Déployer », cliquer « Connecter GitHub » : nouvelle autorisation, retour avec message « GitHub connecté » et installation(s) disponibles.
3. Vérifier que `Continuer` est indisponible avant connexion et choix d'un dépôt/branche valide.
4. Cliquer « Associer au compte AUTOKOM » pour autoriser ensuite la connexion GitHub du compte existant.
5. Se déconnecter puis se reconnecter via le bouton GitHub ; la session HttpOnly doit être établie sans JWT dans l'URL ou le stockage durable.
6. Utiliser « S'inscrire avec GitHub » uniquement pour un compte GitHub non encore lié ; e-mail principal vérifié et autorisations GitHub nécessaires.
7. Vérifier les journaux filtrés sans montrer code, state ni jeton.
8. Vérifier le chargement des dépôts réels, sans dépôt fictif en mode API.

**Limite de validation locale :** tests JavaScript et tests CLI Python possibles ; les tests Django avec base MySQL et la vraie autorisation GitHub sont à exécuter dans les environnements adaptés. Ne pas lancer les tests MySQL directement sur la base de production.
