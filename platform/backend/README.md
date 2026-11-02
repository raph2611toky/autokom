# AUTOKOM Platform — Backend Django multi-applications

Ce projet concerne **AUTOKOM Platform**, distinct d'AUTOKOM Core/CLI. L'infrastructure gérée par AUTOKOM Core reste DevSecOps ; les utilisateurs de la plateforme choisissent le mode DevOps ou DevSecOps de leur pipeline applicatif parmi les options permises.

## Arborescence des applications

```text
backend/
├── config/                           # settings.py, urls.py, ASGI, WSGI
├── apps/
│   ├── accounts/
│   │   ├── admin/                    # Administrateur natif Django, AUTH_USER_MODEL
│   │   └── user/                     # Clients, JWT, endpoints et drf-yasg
│   ├── organizations/                # App autonome
│   ├── projects/                     # Regroupement, pas une app Django
│   │   ├── integrations/             # Connexions Git et webhooks
│   │   ├── project/                  # Projets et membres
│   │   ├── environments/             # Environnements et variables
│   │   ├── configurations/           # Configuration de déploiement
│   │   └── secrets/                  # Références de secrets
│   ├── deployments/                  # Regroupement
│   │   ├── deployment/               # Déploiements et approbations
│   │   ├── pipelines/                # Étapes, logs, findings
│   │   ├── releases/                 # Images et versions
│   │   ├── sites/                    # Applications hébergées
│   │   └── domains/                  # Domaines et TLS
│   ├── infrastructure/               # Regroupement
│   │   ├── tools/                    # Registre de capacités des outils
│   │   ├── resources/                # Plans, quotas, allocations
│   │   ├── policies/                 # Politiques de pipeline
│   │   ├── operations/               # Tâches asynchrones
│   │   └── monitoring/               # Suivi et alertes
│   └── assistance/                   # Regroupement
│       ├── ai/                       # Diagnostics et propositions IA
│       ├── notifications/            # Notifications
│       └── auditing/                 # Audits
├── helpers/                           # Réponses, erreurs, sécurité, e-mails
├── templates/                         # Swagger Login, 404, e-mails; pas de templates admin personnalisés
├── media/                             # Médias utilisateurs
├── statics/                           # Ressources statiques supplémentaires
├── tests/                             # Tests structurels du backend
├── manage.py
├── .env.example
└── requirements.txt
```

Chaque application concrète, y compris celles pour lesquelles les API ne sont pas encore développées, possède :

```text
<app>/
├── __init__.py
├── admin.py
├── apps.py                         # AppConfig.name = chemin complet, label unique
├── migrations/__init__.py
├── models.py                       # Modèles directs, pas de models/
├── permissions.py
├── serializers.py                   # Réservé sauf comptes clients
├── services.py                      # Réservé sauf comptes clients
├── swagger/__init__.py              # Un module distinct par future APIView
├── tests.py
├── urls.py                          # urlpatterns vide sauf comptes clients
└── views.py                         # Réservé sauf comptes clients
```

## Règles du backend

1. Deux catégories de comptes : administrateurs natifs Django (`accounts/admin`) et clients (`accounts/user`). Les rôles de projet/organisation sont attachés aux clients ; ils n'accordent pas l'accès système.
2. **APIView exclusivement**, une seule méthode HTTP par classe. Ne jamais utiliser `ViewSet`, `GenericAPIView` ou vues génériques DRF dans les API métier.
3. Swagger **drf-yasg**, documentation spécifique à chaque vue ; les enveloppes et statuts documentés doivent correspondre aux retours des vues. Les requêtes `multipart/form-data` sont décrites par `manual_parameters` (in `form`) **sans `request_body`**.
4. Utiliser des **imports absolus** (`from apps.projects.project.models import Project`). Aucune importation relative de type `from .swagger`.
5. **MySQL seulement**, développement, tests et production. Pas de SQLite.
6. Chaque modèle a son propre `__str__` et des champs explicitement déclarés, pas de modèle parent abstrait générique.
7. Les pages Swagger nécessitent une session administrateur, via `templates/swagger_login.html`. Les 404 API en production renvoient du JSON ; les autres 404 une page HTML.
8. Les applications autres que `accounts/user` ne contiennent actuellement **que leurs modèles, leurs enregistrements dans Django Admin et des squelettes vides** (aucune API métier factice).

## Installation avec MySQL

Préparer Python 3.11+ et MySQL 8.x, puis créer une base, un compte MySQL et les droits adéquats.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Éditer la configuration MySQL et les secrets dans .env
python verify_structure.py
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

La commande `makemigrations` sans labels explicites couvre toutes les sous-applications installées. Pour la validation :

```bash
python manage.py makemigrations --check --dry-run
python manage.py test apps.accounts.user tests
```

L'exécution des tests de base de données nécessite un utilisateur MySQL autorisé à créer et supprimer la base de test définie par `DB_TEST_NAME`.

## Routes actuelles

- Comptes clients : `/api/auth/` (inscription, e-mail, connexion, JWT, sessions, profil et avatar multipart).
- Documentation privée : `/docs/login/`, `/api/docs/`, `/api/redoc/`, `/api/schema.json`.
- Administration Django : `/admin/`.

Les autres sous-applications n'exposent pas encore de routes métier, conformément à cette étape.

## Important : migrations

L'archive n'inclut aucune migration numérotée non testée. Elle cible une nouvelle base MySQL. Ne pas appliquer directement sur une base dont les anciens labels Django ont déjà été migrés : le changement de labels nécessite une procédure contrôlée de migration historique et de contenu.

Se référer à [CHANGEMENTS.md](CHANGEMENTS.md) pour la liste des corrections et les limites de validation.
