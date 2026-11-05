# AUTOKOM — Authentification, projets et déploiement réels

Correctif conçu pour la version publiée début novembre 2026, après le correctif du logo AUTOKOM.

## Modifications sans changement de design

- `Register.jsx` distingue la réponse HTTP 201 d'une erreur 400, affiche les erreurs `errors.email`, `errors.password_confirm`, etc., et redirige vers `/verify-email` après création réelle ; pas de remise à zéro des mots de passe sur un formulaire en erreur.
- `VerifyEmail.jsx` propose une page d'attente lisible et un renvoi via `/api/auth/email/resend/`. Le token de vérification est retiré de l'URL après lecture ; il n'est jamais stocké.
- Les pages de sélection GitHub, projets et sites conservent les composants, les classes CSS, les thèmes et la maquette d'origine en mode frontend-only. Elles utilisent exclusivement l'API Django lorsque `VITE_AUTOKOM_API_ENABLED=true` et `VITE_AUTOKOM_FRONTEND_ONLY=false`.
- Parcours GitHub réel : statut, autorisation OAuth, installations, dépôts, branches et dossiers depuis l'API Django protégée. Aucun dépôt de démonstration en mode API.
- Gestion des projets : organisations, création, mise à jour des configurations, environnements, archive, historique réel et logs Jenkins.
- Séparation stricte « enregistrer → finaliser et prévisualiser → lancer réellement Jenkins après confirmation ». Aucune publication n'est déclenchée par un bouton d'aperçu.
- Domaines initiaux : preuve de vérification créée dès la réservation ; noms de services AUTOKOM exclus des domaines clients.
- Aucune modification des feuilles CSS, animations, logos ou modes DevOps/DevSecOps de l'interface statique.

## Contrats backend effectivement disponibles

| Fonction | Route Django | Comportement |
|---|---|---|
| Inscription | `POST /api/auth/register/` | 201 + `verification_email_sent` |
| Vérification | `POST /api/auth/email/verify/` | success + message |
| Renvoi e-mail | `POST /api/auth/email/resend/` | réponse neutre |
| Organisations | `GET /api/projects/organizations/` | `{organizations:[...]}` |
| GitHub App | `POST /api/projects/git/github/start/` | URL OAuth |
| Dépôts | `GET /api/projects/git/github/repositories/` | dépôts autorisés |
| Dossiers (nouveau) | `GET /api/projects/git/github/directories/` | dossiers du dépôt, sans contenu des fichiers |
| Projets | `GET /api/projects/` | pagination `{projects, total}` |
| Création | `POST /api/projects/create/` | `organization_id`, `installation_id`, `repository_id`, `name` |
| Configurations | `GET/POST/PATCH /api/projects/<id>/environments/<id>/configurations/...` | brouillons versionnés |
| Finalisation | `POST .../configurations/<config_id>/finalize/` | droits et runner contrôlés |
| Prévisualisation | `GET .../deployments/preview/` | ne lance rien |
| Jenkins | `POST .../deployments/launch/` | en-tête `Idempotency-Key`, consentement explicite |
| Statuts / logs | `GET .../deployments/`, `GET .../deployments/<id>/logs/` | sorties réelles |
| Archive | `POST /api/projects/<id>/archive/` | archive, ne supprime pas définitivement |

## Limitations connues, conservées honnêtement

- Le backend n'autorise actuellement que le **build Dockerfile** dans `check_configuration()`. L'auto-détection Vite/Django de la maquette n'est **pas** considérée comme un build actif. Les chemins sont lus via GitHub, mais l'existence du Dockerfile est validée lors du pipeline.
- Le mode DevSecOps nécessite une **autorisation de plan** `DEVSECOPS` du backend ; l'offre gratuite initiale est DevOps. L'interface conserve les deux modes et affiche les refus de finalisation du serveur.
- Les offres payantes de la maquette ne sont pas un paiement réel. La sélection d'une offre payante ne crée pas un abonnement et ne sera pas annoncée comme activée avant l'intégration du billing/entitlement.
- La connexion GitHub sur les écrans de **login/signup** reste une fonctionnalité distincte, non activée par ce correctif. Ici, OAuth est utilisé pour **autoriser les dépôts après connexion**.
- Les intégrations de services dans les paramètres avancés ne sont pas toutes configurables via les modèles Django ; les contrôles réels restent côté backend et Jenkins.
- L'endpoint GitHub dossiers exige un vrai compte vérifié, une installation et un dépôt autorisés. Il limite le nombre de requêtes et interdit les chemins traversants. Les résultats volumineux restent soumis aux limites de l'API GitHub.
- En cas de perte de réponse réseau pendant la création d'un projet, vérifier « Mes projets » avant de réessayer pour éviter un doublon ; le backend ne fournit pas encore d'Idempotency-Key pour la création, contrairement au lancement de pipeline.

## Kali — patch Git

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Auth_Projects_Dynamic_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Auth_Projects_Dynamic_2026-11-05.patch
npm --prefix platform/frontend test
python3 -m compileall -q platform/backend/apps/projects platform/backend/apps/deployments/domains
git diff --check
```

Vérifier les changements avant commit :

```bash
git status --short
git diff --stat
git add platform/frontend/src platform/frontend/tests \
  platform/backend/apps/projects platform/backend/apps/deployments/domains \
  platform/backend/config/settings.py docs/AUTOKOM-AUTH-PROJECTS-DYNAMIC-2026-11-05.md
git commit -m "feat: real Django projects and GitHub deploy workflow with register fixes"
git push origin main
```

## Ubuntu AWS — ordre de déploiement

Ne pas rejouer `frontend setup`, Certbot ou NodeSource. Le site `auto.ikom.mg` est déjà publié avec HTTPS.

```bash
sudo autokom update
sudo autokom app deploy --check
```

Avant `backend deploy`, réaliser les tests Django dans l'environnement Python connecté à une **base de test isolée** (pas la base de production) :

```bash
python manage.py test apps.projects.git.test_directories apps.projects.project.test_reserve_domain
```

Puis :

```bash
sudo autokom app deploy
sudo autokom app status
sudo autokom app frontend env show
```

**Activation GitHub :** l'ancien `frontend.env` contient `VITE_AUTOKOM_GITHUB_ENABLED=false`. Dès que la GitHub App est bien enregistrée, que le callback `https://backend.auto.ikom.mg/api/projects/git/github/callback/` correspond à sa configuration et que l'installation est autorisée sur les dépôts, passer la variable à `true` via :

```bash
sudo autokom app frontend env edit VITE_AUTOKOM_GITHUB_ENABLED
```

Saisir `true` dans le menu. Ne placer aucun secret dans un fichier `VITE_*`.

```bash
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
curl -I https://auto.ikom.mg/
curl -I https://auto.ikom.mg/autokom-logo.png
```

**Parcours de recette :** connexion avec le compte déjà vérifié → Mes projets → Déployer → connexion GitHub App → choix d'organisation, installation, dépôt, branche, dossier → Enregistrer → Mes projets → Modifier → Préparer le pipeline (Django doit valider un Dockerfile et les droits) → Lancer Jenkins avec confirmation explicite → Mes projets → détails et logs. En mode connecté aucun dépôt de démonstration ne doit apparaître.

**Rollback frontend** en cas de problème après publication :

```bash
sudo autokom app frontend rollback --check
sudo autokom app frontend rollback --apply
```

Le rollback frontend ne restaure pas automatiquement le code du backend. Si des tests backend échouent, ne pas appliquer son déploiement, ni la publication frontend qui dépend des nouveaux endpoints.
