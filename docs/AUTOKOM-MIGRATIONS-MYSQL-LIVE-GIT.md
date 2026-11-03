# AUTOKOM — migrations, MySQL, journal dynamique et mise à jour Git

## Causes confirmées par les logs du 3 novembre 2026

1. `makemigrations --check --dry-run` retourne 1 parce que les modèles ont changé sans fichiers de migration versionnés. Les migrations manquantes concernent `configurations` et `integrations` (GitHub). **Ce n'est pas un échec de `migrate` et aucune modification MySQL n'a été appliquée.**
2. Django signale une erreur MySQL 1045 pour `autokom`@`localhost`. `check --deploy` ne teste pas la connexion DB. L'erreur peut provenir du mot de passe du service, d'une différence entre le `.env` source et le `.env` utilisé par systemd, ou d'une entrée MySQL `user`@`host` différente. Ne pas inventer de mot de passe ou recréer la base existante.
3. `Trivy` a indiqué 0 manifeste de dépendances reconnu (`num=0`), donc le succès du scan n'est **pas** une preuve que les vulnérabilités des dépendances Python ont été examinées. Les contrôles SCA de dépendances restent à compléter par un lockfile/SBOM approprié.
4. Les deux avertissements HSTS (`W005`, `W021`) sont de simples avertissements. Ne pas activer HSTS sur tous les sous-domaines d'AUTOKOM sans valider que tous utilisent HTTPS.

## Ce que le patch corrige

- Étape MySQL authentifiée, en tant que compte de service, **avant** l'analyse coûteuse ; échec explicite sans révéler `DB_PASSWORD`.
- `autokom app doctor` pour une connexion réelle `SELECT 1`, sans mutation et sans afficher de secrets.
- Rendu animé de `autokom app deploy` conservé, avec **trois dernières sorties réelles** en vert, suppression de l'aperçu à la fin des étapes et résumé des dernières lignes en cas d'échec.
- `autokom update` utilise toujours `origin/main` et le Device Flow GitHub ; il présente les **vraies** modifications `+/-` issues de `git diff --numstat`, avec couleurs vert/rouge en terminal interactif avant de procéder au fast-forward.
- Un outil pour **générer les migrations sur Kali**, sans appliquer de SQL en production.

## Déploiement sans risque

Sur Kali, appliquer le patch puis générer les migrations **sur le code le plus récent**, en utilisant le Python de développement et non celui du serveur. Relire les nouvelles migrations et committer les fichiers sur `main`. Sans connexion DB locale, Django peut afficher un avertissement de vérification de l'historique ; contrôler que le schéma et les dépendances sont cohérents avant publication.

Sur Ubuntu, `sudo autokom update` récupère le code et les migrations. Exécuter ensuite `sudo autokom app doctor`. Si MySQL refuse encore la connexion, comparer **sans afficher les mots de passe** `/etc/autokom/secrets/backend.env` et `/etc/autokom/platform-backend.env`, puis vérifier le compte et l'hôte autorisés avec l'administrateur MySQL. Ne pas supprimer les tables et ne pas utiliser `migrate --fake` sans justification.

Après un diagnostic MySQL réussi : `sudo autokom app deploy --check`, puis `sudo autokom app deploy`. Ce dernier applique les migrations versionnées sous le compte système `autokom` et démarre Gunicorn.

## Précautions

- Ne jamais committer un `.env`, des identifiants MySQL ou des tokens GitHub.
- Ne jamais générer des migrations en production ; le pipeline bloque volontairement si elles manquent.
- Ne pas activer HSTS preload ni includeSubDomains automatiquement.
- Ni service systemd, ni Nginx, ni MySQL ne sont modifiés par l'installation de ce patch.
