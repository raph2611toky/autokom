# Modifications V2 — plateforme clients uniquement

- Regroupement en 17 apps Django indépendantes.
- Modèles métier réduits à 27, cohérents avec le guide fonctionnel.
- Suppression des apps `infrastructure/{tools,monitoring,policies,operations,resources}`.
- Nouvelles apps `plans/catalog`, `plans/entitlements`.
- Mode DevOps inclus et ressources de base gratuites ; droits premium DevSecOps et ressources supplémentaires, sans facturation en ligne pour l'instant.
- Déploiement manuel ou automatique Git ; stockage des stages de pipeline et références des logs/outils externes.
- Comptes : jetons de vérification et de récupération fusionnés dans `UserActionToken`; autres API de comptes conservées.
- Conservation de MySQL, du panneau admin natif, de drf-yasg et des conventions APIView.
- Important : les étiquettes Django historiques réutilisées n'équivalent pas à une migration de données ; revue des migrations et de la base impérative.
