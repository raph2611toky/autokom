# AUTOKOM — Source Django « secrets » et terminal live

## Deux types de secrets à ne pas confondre

- Code Django : `platform/backend/apps/projects/secrets/` ; doit être présent dans Git.
- Vraies informations sensibles : `/etc/autokom/secrets/backend.env` ; ne doivent **jamais** être présentes dans Git.

Le `.gitignore` à la racine `secrets/` ignorait le dossier source à n'importe quel niveau. L'exception `!platform/backend/apps/projects/secrets/` n'autorise que ce dossier de code Python. Les règles `.env`, `.env.*`, `*.key`, `*.pem` restent actives.

## Accès en lecture

AUTOKOM donne à l'utilisateur systemd `autokom` un accès en lecture au package Django uniquement. La configuration des ACL a été ajoutée au lancement de `autokom app deploy` pour couvrir une installation déjà effectuée.

## Interface live

Pendant `autokom app deploy` et `autokom app setup`, la même animation de chargement est maintenue, avec en dessous **les trois lignes de sortie les plus récentes en vert**. L'ensemble est effacé à chaque fin d'étape ; la sortie complète et non filtrée est enregistrée dans `/var/log/autokom/platform/`. Les lignes contenant des termes associés aux identifiants sont masquées dans l'aperçu terminal. Les journaux complets sont accessibles uniquement à root.

## Déploiement

- Appliquer le patch dans Kali et le publier sur `main`.
- Sur Ubuntu : `sudo autokom update` puis `sudo autokom app deploy --check` et `sudo autokom app deploy`.
- Il n'est pas nécessaire de relancer `autokom app setup` ni de toucher à Nginx ou MySQL.
- Si `deploy --check` échoue, lire le nouveau journal et corriger l'erreur sans contourner Semgrep/Trivy/Django.
