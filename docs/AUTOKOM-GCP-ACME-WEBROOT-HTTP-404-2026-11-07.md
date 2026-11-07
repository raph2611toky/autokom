# AUTOKOM GCP — Certbot : validation HTTP-01 renvoyant 404

Le 7 novembre 2026, `autokom install` s'arrête pendant le HTTPS de `prometheus.ikom.mg`, après installation de Prometheus et Grafana. La réponse ACME est `Invalid response ... : 404` depuis 136.116.89.163. Le DNS et l'accès WAN 80 fonctionnent suffisamment pour recevoir cette réponse ; il s'agit d'un mauvais routage Nginx du défi et non d'un nouveau problème de pare-feu. Ne pas retirer les comptes, les tableaux de bord, les données Prometheus, les paramètres UFW ou les certificats existants.

## Correctif intégré

- Le virtual host AUTOKOM HTTP en attente de TLS expose **uniquement** `/.well-known/acme-challenge/` depuis `/var/www/autokom-acme` ; toutes les autres routes restent HTTP 503.
- AUTOKOM teste le chemin HTTP depuis le *bon Host* local sur `127.0.0.1:80` avec un fichier de contrôle aléatoire. Si Nginx ne renvoie pas HTTP 200 et le corps exact, AUTOKOM **ne lance pas** Certbot.
- Après validation locale, `certbot certonly --webroot -w /var/www/autokom-acme` écrit les défis lui-même sans modifier temporairement les blocs Nginx.
- Une fois le certificat valide, AUTOKOM active le proxy HTTPS, avec authentification Basic côté Prometheus. Le bloc HTTP conserve l'exception ACME pour les renouvellements automatiques et redirige les autres chemins vers HTTPS.
- Les `TLS PENDING` historiques (version précédente) sont reconnus **seulement** s'ils correspondent exactement au contenu du vhost AUTOKOM attendu, avec le lien symbolique pointant sur le bon fichier. Les vhosts étrangers ne sont pas remplacés.
- La destination ACME est racine:racine, non inscriptible par les autres comptes, sans lien symbolique. Les fichiers de contrôle temporaires sont supprimés après le test. Aucune donnée confidentielle ou mot de passe n'est publié dans le webroot.
- Le diagnostic de la nouvelle tentative est extrait du journal Certbot, en masquant le jeton du défi.

## Application

Sur Kali, à la racine du dépôt : extraire le ZIP différentiel, exécuter les tests ciblés et `git diff --check`, versionner les fichiers explicitement et pousser vers `main`.

Sur la VM GCP :

```sh
sudo autokom update
sudo autokom install
```

Les étapes déjà conformes (fondation, Jenkins et plugins) seront ignorées. Si un certificat a déjà été émis mais que son proxy n'a pas été publié, il sera réutilisé. Ne pas forcer une nouvelle demande en boucle en cas d'échec.

### Contrôle après installation

```sh
sudo nginx -t
sudo systemctl is-active nginx prometheus grafana-server jenkins
sudo certbot certificates
curl -4I --connect-timeout 8 https://prometheus.ikom.mg/
curl -4I --connect-timeout 8 https://grafana.ikom.mg/
```

Prometheus est protégé derrière l'authentification HTTP ; un **401** sans identifiants peut être normal. Grafana peut répondre **302** vers `/login`. Ne pas afficher ni transmettre les mots de passe.

Si le contrôle HTTP local réussit mais que Certbot échoue depuis Internet, vérifier la route VPC/CDN et le dernier message ACME de la tentative. L'absence de 404 local n'atteste pas, à elle seule, l'accessibilité depuis tous les réseaux publics.

**Vérification locale hors production :** tests Python ciblés, syntaxe Nginx et réponse réelle HTTP 200 du webroot avec serveur Nginx isolé sur port éphémère ; les deux comportements HTTP 503 et 404 restent contrôlés. Pas d'essai ACME réel sur le GCP de l'administrateur depuis cet environnement.
