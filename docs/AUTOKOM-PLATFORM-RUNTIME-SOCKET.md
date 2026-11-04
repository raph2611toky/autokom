# AUTOKOM Core — Runtime de déploiement des clients (AWS/Docker)

Le code système du runtime est **indépendant de Django** : `scripts/lib/autokom_platform_broker.py` n'accepte que des requêtes JSON typées via un socket Unix, avec contrôle de l'UID du client. Aucune action shell arbitraire ne peut être fournie. Il dialogue avec Jenkins via son API, charge des images Docker locales, publie des ports sur 127.0.0.1 et gère les vhosts Nginx/Certbot par domaine.

Les sous-domaines IKOM sont créés seulement via API Cloudflare (token DNS limité à la zone) après délégation effective. Les domaines externes nécessitent TXT `_autokom-challenge.<hostname>` et A vers l'IP ingress confirmés sur des résolveurs publics.

Pour l'installation et la validation, voir `platform/backend/GUIDE_RUNTIME_DOCKER_JENKINS_SOCKET_DOMAINES.md`. Ne pas donner accès Docker à Gunicorn, au worker Django ni au Jenkins builder. La haute disponibilité et le registre premium restent désactivés.

Après extraction : `python3 scripts/dev/activate_runtime_cli.py --repo "$PWD" --apply` puis commit sur `main`. Le script modifie uniquement la déclaration et la délégation de `autokom runtime`, refuse les versions inattendues du Core et conserve le système d'authentification et les autres commandes.
