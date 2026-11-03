# AUTOKOM Platform — backend clients (V2)

## Périmètre

Socle modèle de la plateforme **clients uniquement** : 17 apps Django, 27 modèles.
Aucune app de configuration serveur, catalogue des outils, pool de machines ou administration Jenkins/Docker.
AUTOKOM Core reste le moteur technique derrière des intégrations internes, à implémenter lors de la phase des services.

## Fonctionnalités conceptuelles

- Deux identités distinctes : administrateur Django système et utilisateur client ; rôles dans les organisations/projets.
- Inscription/connexion clients déjà présents, avec 16 APIView, une méthode HTTP par classe.
- Projets Git, environnements, variables et références de secrets.
- Déploiements **manuels** ou après webhook Git, étapes visibles et journaux externes référencés.
- DevOps inclus par défaut ; DevSecOps premium. Offres et droits accordés séparément.
- Petites ressources gratuites ; ressources supplémentaires premium configurables.
- Paiements **non implémentés** ; seul un administrateur système peut activer manuellement un droit premium.
- Sites, domaines propres ou sous-domaines AUTOKOM, versions, diagnostic IA, notifications.

## Structure

`accounts/{admin,user}`, `organizations`, `projects/{git,project,environments,configurations,secrets}`, `deployments/{runs,releases,sites,domains}`, `plans/{catalog,entitlements}`, `assistance/{ai,notifications,auditing}`.
Chaque app réelle possède ses propres `apps.py`, `models.py`, `admin.py`, `views.py`, `serializers.py`, `urls.py`, `permissions.py`, `services.py`, `tests.py`, `swagger/`, `migrations/`. Les APIs autres que celles des comptes restent vides par conception.

## Installation (MySQL obligatoire dans tous les environnements)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Compléter les variables MySQL, secret Django et SMTP
python verify_structure.py
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py test tests.test_platform_models apps.accounts.user
```

**Ne lancez pas les migrations contre une base MySQL créée avec les anciennes apps sans un plan de migration de données.** Préférez une nouvelle base MySQL vide si les anciens modèles n'ont jamais été utilisés. L'installation automatisée ne supprime **aucune table SQL**.

## Conventions

- DRF `APIView` exclusivement, une méthode HTTP par classe, exceptions gérées, imports Python absolus `apps...`.
- `drf-yasg` et fichiers Swagger par vue ; schémas alignés sur les réponses effectives.
- Multipart : `manual_parameters` sans `request_body`.
- MySQL uniquement (y compris DEBUG). Modèles explicites sans classe abstraite `TimestampedUUIDModel`.
- Chaque modèle possède son propre `__str__`, `admin.py` séparé et des migrations Django par app.
- Ne pas exposer de tokens Git ni secrets dans des JSONField ou logs.

## Contrôles restant à faire

Contrôles statiques réalisés localement. Exécution réelle de Django, création des migrations et tests MySQL à effectuer sur la machine cible (l'environnement de création ne dispose pas des paquets Django/MySQL).

## Nettoyage sans sauvegarde

Utiliser `scripts/nettoyer_et_installer.py` : simulation par défaut, application explicite avec `--apply`. Script destructif pour les anciens fichiers de code, **sans backup** ; préserve les fichiers `.env`, les médias et les environnements virtuels. La base MySQL n'est pas touchée.
