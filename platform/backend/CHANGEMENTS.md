# AUTOKOM Platform — modifications de structure

## Pourquoi cette version

L'ancien regroupement `apps/projects/models/`, etc. n'était pas composé de véritables sous-applications Django. Dans cette nouvelle version, chaque sous-app possède **son propre `apps.py`, `models.py`, `admin.py`, `views.py`, `serializers.py`, `urls.py`, `permissions.py`, `services.py`, `tests.py`, `swagger/` et `migrations/`**. Les répertoires de domaine (`projects`, `deployments`, `infrastructure`, `assistance`, `accounts`) ne sont pas enregistrés dans `INSTALLED_APPS` à la place de leurs sous-apps.

## Conservé

- 21 applications Django indépendantes, 67 modèles, 67 méthodes `__str__`, noms SQL `db_table` explicites.
- Un seul principal d'authentification Django : `apps.accounts.admin.models.Administrator`. Les clients restent `apps.accounts.user.models.User`, avec JWT client séparé.
- API de comptes fonctionnellement identique : uniquement des `APIView`, une méthode HTTP par classe, vues et documentations Swagger séparées, ainsi qu'un traitement multipart documenté avec `manual_parameters` et sans `request_body`.
- Authentification préalable du Swagger par la session administrateur Django, page HTML de connexion et 404 JSON pour `/api/` en production.
- MySQL obligatoire même en développement/tests. Aucun SQLite. Pas de modèle abstrait de type `TimestampedUUIDModel`.

## Modifié

- Tous les sous-modèles antérieurement regroupés dans `models/` ont été déplacés vers un seul `models.py` **par véritable application Django**.
- Chaque `AppConfig.name` correspond à son chemin complet Python ; chaque `AppConfig.label` est unique.
- `INSTALLED_APPS` référence les 21 applications concrètes.
- Les relations `ForeignKey`/`OneToOneField` ont été réalignées avec leurs nouveaux labels Django.
- Les imports du compte client vers les modèles de projet ont été mis à jour en chemins absolus.
- Tous les sous-modules hors comptes disposent de leurs fichiers REST/Swagger de démarrage, volontairement sans endpoints factices.
- Ajout de `verify_structure.py` et des contrôles de la hiérarchie dans `tests/`.

## Attention aux bases de données existantes

Le déplacement de modèles entre labels Django **change le graphe de migrations et les `django_content_type`**, même si les noms de tables physiques sont conservés. Cette livraison vise une **base MySQL neuve** ; un système déjà migré demande un plan de migration de données contrôlé. Aucun fichier de migration numéroté n'a été généré artificiellement. Il faut générer et tester de vraies migrations Django avec MySQL.

## Validation

- Réalisé : analyses AST, arborescence des fichiers, unicité des 21 labels, présence et liens entre les 67 modèles et leurs 99 relations inter-modèles, imports absolus, noms de tables explicites, configuration MySQL.
- Non réalisé ici : `manage.py check`, `makemigrations`, migrations effectives, `runserver` et tests d'intégration. Ces actions requièrent Django, les dépendances Python et un service MySQL accessibles (non disponibles dans cet environnement).
