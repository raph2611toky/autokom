# AUTOKOM Core — secrets centralisés et migration progressive `ikom.mg`

## Principes

- Uniquement sur l'hôte : `/etc/autokom/secrets/` (`root:0700`), avec les fichiers `backend.env`, `runtime.env`, `frontend.env` et `cloudflare-dns.env` (`root:0600`). Aucun `.env.example` n'est stocké dans Git.
- `frontend.env` est une configuration préparatoire : le module n'injecte pas automatiquement des secrets dans le bundle React/Vite. Ne jamais exposer des secrets à Vite via `VITE_*`.
- `app backend env setup` **adopte les identifiants du service actuellement actif** `/etc/autokom/platform-backend.env`, si celui-ci existe ; il ne recrée ni utilisateur MySQL, ni base. Si l'ancienne version et le fichier centralisé sont différents, l'administrateur tranche via un menu interactif.
- Le broker garde les privilèges nécessaires ; Django ne reçoit pas de privilèges root ou Docker. Les services systemd utilisent les nouveaux chemins, via des drop-ins si les unités existent déjà. Le daemon-reload n'est pas un redémarrage.
- Pour revenir en arrière en cas de régression, les configurations précédentes demeurent disponibles dans `/etc/autokom/secrets/archive/` (mode `0700`, sauvegardes `0600`), et les anciens `.env` **ne sont pas supprimés automatiquement**.

## Commandes

```bash
sudo autokom app backend env setup
sudo autokom app backend env show
sudo autokom app backend env edit
sudo autokom app backend env delete
sudo autokom app frontend env setup
sudo autokom runtime env setup
sudo autokom runtime env show
sudo autokom runtime env edit AUTOKOM_INGRESS_IPV4
sudo autokom runtime env delete
sudo autokom dns cloudflare env setup
sudo autokom dns cloudflare env show
sudo autokom dns cloudflare list
```

Les variables sensibles sont saisies via `getpass`, jamais via `--value`, jamais affichées dans `show`. `edit` et `delete` sont interactifs et sauvegardent la version antérieure. Aucune suppression silencieuse d'un fichier entier. Les menus utilisent le composant AUTOKOM avec flèches `↑/↓` lorsque le terminal le permet.

La CLI inspecte `app-runtime.json`, Jenkins systemd, la configuration Cloudflare déjà présente et tente la métadonnée AWS IMDSv2 (temps limité) pour préremplir les valeurs détectables. Les identifiants Jenkins / token Cloudflare doivent être saisis et sont **impossibles à déduire automatiquement**. Le chemin vers la création d'un jeton limité est affiché : https://dash.cloudflare.com/profile/api-tokens . Permissions : Zone DNS Edit et Zone Read, limitées à la zone `ikom.mg`.

## Mise à jour sans interruption

1. Sur le dépôt de développement (Kali), extraire le patch et exécuter `python3 scripts/dev/audit_git_env.py --apply` ; puis **désindexer** tous les `.env` / `.env.example` avec `git rm --cached --ignore-unmatch ...`. Ne pas versionner les copies d'environnement.
2. Publier `main` ; sur AWS, `sudo autokom update`.
3. **Avant `app deploy`** : `sudo autokom app backend env setup`, puis `sudo autokom app backend env show` et `sudo autokom app doctor --no-repair`. Ne pas recopier les anciens identifiants depuis `/etc/autokom/secrets/backend.env` si `/etc/autokom/platform-backend.env` est celui qui fonctionne.
4. `sudo autokom runtime env setup`, puis `sudo autokom dns cloudflare env setup` ; vérifier `sudo autokom runtime env show` et `sudo autokom dns cloudflare list`.
5. `sudo autokom runtime setup --check`, puis seulement lorsque les prérequis sont prêts `--apply` : il peut redémarrer le broker et le worker. `sudo autokom app deploy --check`, puis `--deploy` en fenêtre contrôlée (Gunicorn redémarrera).
6. Une fois les processus effectivement redémarrés avec le nouveau chemin, les anciens fichiers de `/etc/autokom/` peuvent être retirés manuellement après inventaire des références systemd. Ils sont volontairement conservés par le patch pour permettre un retour arrière.

## Migration du domaine

**Inventaire sans aucune modification :**

```bash
autokom domains migrate --check
autokom domains migrate --source dia.ik0m.tech --check
```

**Préparation requise avant application :**

- Cloudflare `ikom.mg` réellement **active**, jeton restreint `Zone Read` + `Zone DNS Edit`, noms de serveurs dot.mg cohérents (vérifier `dig NS ikom.mg +short`).
- `AUTOKOM_INGRESS_IPV4` est l'IP publique AWS réelle et stable (préférer Elastic IP).
- HTTP 80 et HTTPS 443 entrants autorisés, Nginx et Certbot opérationnels.
- Le vhost de l'ancien domaine doit être **simple** : `proxy_pass http://127.0.0.1:PORT;` unique. Les sites statiques, plusieurs upstreams et configurations complexes restent à auditer séparément.
- Le domaine `infra.ikom.mg` et les noms `ns1`/`ns2` sont protégés : jamais migrés ni modifiés par cette commande.

**Exécution confirmée par application :**

```bash
sudo autokom domains migrate --source dia.ik0m.tech --apply
```

1. Détecte automatiquement l'ancien vhost et son port, le nom cible et l'enregistrement Cloudflare actuel.
2. N'écrase pas les enregistrements Cloudflare existants incompatibles ; crée un A « DNS only » lorsque absent.
3. Attend la propagation DNS : si elle n'est pas visible, **ne coupe rien** et demande une nouvelle exécution plus tard.
4. Crée un **nouveau fichier Nginx séparé**, vérifie `nginx -t` avant rechargement, puis demande un certificat avec Certbot `--webroot`.
5. Ajoute le HTTPS dans la nouvelle configuration ; pour le backend Django, autorise également le nouveau `ALLOWED_HOSTS`/CSRF ; pour le frontend principal, ajoute CORS/CSRF sans supprimer l'ancien.
6. Conserve en permanence le domaine `*.ik0m.tech` et son vhost. **Ne modifie pas le registrar dot.mg**.
7. Le script ne promet pas de migrer les cookies, les URLs OAuth de chaque application, les intégrations externes ou les mécanismes de messagerie : ces composants doivent être validés avant bascule définitive. Les applications dont le vhost n'est pas simple sont laissées intactes.

**Important sur la capture Cloudflare :** la zone `ikom.mg` étant proxifiée, une interrogation `dig A ikom.mg` renvoie des IP Cloudflare et non `56.228.86.160` ; c'est attendu. L'enregistrement `infra.ikom.mg NS ns1.ikom.mg` déjà présent délègue actuellement vers AWS seul ; attendre le second NS GCP pour une configuration DNS résiliente.

## Sécurité de Git

Le patch **ne contient aucun `.env` ni `.env.example`**. Le script d'audit préserve les exceptions existantes permettant à Git de suivre `apps/projects/secrets/*.py` : ces fichiers Python sont du code, et ne doivent pas être ignorés. Si un fichier confidentiel avait déjà été versionné, le désindexer ne supprime pas son historique : effectuer la rotation des identifiants et analyser l'historique Git.
