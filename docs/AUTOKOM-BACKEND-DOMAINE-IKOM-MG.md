# AUTOKOM — Domaine secondaire du backend Django (`ikom.mg`)

## Portée

Commande : `sudo autokom app backend domain [--check|--apply|--status]`.

- Découvre le backend *déjà déployé* dans `/etc/autokom/app-runtime.json` (systemd/Gunicorn).
- Lit la zone DNS Cloudflare et l'IP de l'interface publique depuis `/etc/autokom/secrets/`.
- **N'installe pas** Django, ne touche pas à MySQL, Jenkins, Docker, BIND9, aux registres d'images ou au frontend.
- Conserve le vhost, le certificat et l'ancien domaine `backend.auto.ik0m.tech`.
- Le nouveau nom par défaut est dérivé de l'ancien domaine et de `CLOUDFLARE_ZONE_NAME`, non codé en dur.
- Prépare un **vhost distinct** et sa configuration HTTPS avec certbot webroot.
- Ajoute uniquement le nouvel hôte aux valeurs `ALLOWED_HOSTS` et `CSRF_TRUSTED_ORIGINS` dans `/etc/autokom/secrets/backend.env`.
- **Ne modifie pas** `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, `GITHUB_APP_CALLBACK_URL`, `GITHUB_AUTH_CALLBACK_URL`, les JWT, la base de données ou les secrets existants.
- Vérifie que `autokom-backend.service` lit le `.env` centralisé avant tout changement.
- Effectue `nginx -t` avant rechargement et rollback Nginx en cas d'échec.
- Si la configuration Django doit changer, effectue un redémarrage contrôlé et restaure le `.env` si l'application ne démarre pas.
- Teste `https://<nouveau-domaine>/api/health/` avec `--resolve <nouveau-domaine>:443:127.0.0.1`, donc sans NAT hairpin AWS. Enregistre ensuite le domaine préféré dans l'état, sans remplacer le domaine historique.
- La commande est **reprenable et idempotente**. Le mode `--check` est en lecture seule. `--apply` nécessite `sudo`, un terminal, l'authentification AUTOKOM et la confirmation `MIGRER`.

## Installation sur le dépôt de développement (Kali)

À la racine d'AUTOKOM :

```bash
unzip -o AUTOKOM_Backend_Domain_IKOM_MG_DIRECT.zip
PYTHONPATH=scripts/lib python3 -m unittest discover -s tests -p 'test_autokom_backend_domain.py' -v
python3 -m py_compile scripts/lib/autokom_backend_domain.py scripts/lib/autokom_app_manager.py scripts/lib/autokom_cli.py
git diff --check
git add scripts/lib/autokom_backend_domain.py scripts/lib/autokom_app_manager.py scripts/lib/autokom_cli.py tests/test_autokom_backend_domain.py docs/AUTOKOM-BACKEND-DOMAINE-IKOM-MG.md
git diff --cached --check
git commit -m 'feat: parallel AUTOKOM backend hostname on ikom.mg'
git push origin main
```

## Installation sur AWS

```bash
cd /opt/autokom/source
sudo autokom update
sudo autokom app backend domain --check
sudo autokom app backend domain --apply
sudo autokom app backend domain --status
sudo autokom app status
```

Le plan actuel correspond, si l'état du serveur le confirme, à :

- Ancien backend : `backend.auto.ik0m.tech`
- Nouveau backend : `backend.auto.ikom.mg`
- Gunicorn : port local `8172`
- Cloudflare `ikom.mg` : **CNAME de `backend.auto.ikom.mg` vers `ikom.mg`** par défaut.

**Attention Cloudflare** : le CNAME vers `ikom.mg` hérite du proxy si la racine est Proxied, même si on voulait du DNS-only. C'est intentionnel et affiché dans le plan. Si Cloudflare bloque la validation HTTP-01 ou si l'API ne doit pas passer par le proxy, préférer `--dns-mode a` pour créer un A DNS-only au moment de la *première* création. Un enregistrement déjà présent n'est jamais supprimé/converti automatiquement. Vérifier le mode SSL/TLS Cloudflare (Full strict recommandé pour HTTPS origin) et les règles WAF si nécessaire.

Exemples :

```bash
sudo autokom app backend domain --check
sudo autokom app backend domain --apply
sudo autokom app backend domain --status
sudo nginx -t
sudo systemctl is-active autokom-backend
curl -sS -i https://backend.auto.ikom.mg/api/health/
curl -sS -i https://backend.auto.ik0m.tech/api/health/
```

Le `/api/health/` Django est une vue **GET** : ne pas utiliser `curl -I` (HEAD) comme preuve de santé sans vérifier son comportement.

## Frontend, OAuth GitHub et déploiements clients

- **Aucune publication de `auto.ikom.mg`** tant que le frontend n'est pas prêt.
- **Aucune bascule de `FRONTEND_URL` ou CORS** : l'ancien frontend reste la source autorisée.
- Les callbacks GitHub existants restent sur le domaine historique. Avant de changer leur domaine dans les variables, enregistrer les nouvelles URL autorisées dans GitHub App, vérifier OAuth et webhooks ; cette commande ne les modifie pas.
- Les pipelines Jenkins, les workers, le socket privilégié et Docker ne sont pas redémarrés ; la phase de validation des déploiements clients sera menée séparément.
- Les futures URLs d'applications clientes sous `apps.auto.ikom.mg` dépendent d'une configuration et de quotas indépendants : **ne pas les activer par la migration du backend**.

## Récupération et sécurité

- En cas de timeout DNS ou Certbot, le vhost HTTP préparatoire reste et la commande peut être relancée. L'ancien domaine reste fonctionnel.
- Les certificats sont renouvelés par Certbot ; ne pas supprimer les fichiers `/etc/letsencrypt/live/` à la main.
- Si `autokom-backend.service` ne lit pas le fichier centralisé, passer par `sudo autokom app backend env setup` avant migration (le setup peut proposer d'adopter les identifiants MySQL qui fonctionnent ; ne jamais les écraser à l'aveugle).
- Les journaux de migration résident dans `/var/log/autokom/platform/domains-migrate-*.log`, permissions 0600 et aperçu temporaire de trois lignes sur terminal.
- La configuration Nginx dédiée est `/etc/nginx/sites-available/autokom-backend-domain-backend.auto.ikom.mg.conf` si ce nom est retenu ; l'ancien `/etc/nginx/sites-enabled/autokom-platform.conf` n'est pas modifié.
- En cas de certificat ou de domaine public encore non fonctionnel, vérifier `sudo nginx -t`, les enregistrements Cloudflare et l'accès externe sans supprimer le domaine existant.
