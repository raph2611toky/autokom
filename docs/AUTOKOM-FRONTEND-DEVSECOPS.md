# AUTOKOM — Frontend React/Vite, reverse proxy et publication DevSecOps

**État :** correctif préparé à partir de l'archive du 04/11/2026, non déployé sur AWS.
Le code des pages, composants, styles, ressources et arborescence React est conservé tel quel.

## 1. Avant toute application

1. Sauvegarder les fichiers Nginx existants et les éventuelles releases du site `auto.ik0m.tech`.
2. Vérifier `nginx -T` et relever tout `server_name auto.ik0m.tech`. Le setup **refuse tout écrasement d'un vhost tiers**.
3. Vérifier que `backend.auto.ik0m.tech` est servi par Django, idéalement sur le même hôte, et que `backend.env` contient les bonnes valeurs `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_URL`.
4. Avoir Node.js >=20.19 ou >=22.12, npm, Nginx, Certbot (si mode certbot), Semgrep, Trivy. Node/npm doivent être disponibles dans le `PATH` système pour le compte builder (ex. `/usr/bin`), pas uniquement dans un nvm root inaccessible.
5. Un agent Jenkins `linux-devsecops` doit exister avec Node, npm, Semgrep et Trivy, mais **sans accès root ni écriture au vhost**.
6. Mettre le code dans une branche de revue, lancer tests et pipeline, puis approuver la PR avant `autokom update`.

## 2. Configuration / prévisualisation

Sur AWS, après intégration du patch :

```bash
sudo autokom update
sudo autokom app frontend setup \
  --domain auto.ik0m.tech \
  --backend-domain backend.auto.ik0m.tech \
  --nginx-mode managed \
  --tls-mode certbot \
  --email exemple@domaine.tld \
  --check
```

Si `auto.ik0m.tech` est **déjà** desservi par un vhost Nginx non géré, choisir `--nginx-mode existing --tls-mode existing` et préparer **manuellement** le root `/var/www/autokom/frontend/current` ainsi que le proxy `location /api/` dans ce vhost. La commande ne l'écrase pas et ne promet pas de faire le routage à sa place. Pour migrer un site déjà actif vers un vhost géré, faire une revue de configuration et une bascule planifiée : ne pas supprimer le vhost existant à l'aveugle.

En mode géré, vérifier DNS A/AAAA, ports 80/443, et accès ACME ; Certbot modifie exclusivement le vhost dédié frontend. Le TLS de l'origine doit rester valide même avec Cloudflare en mode Full (strict).

Pour appliquer, remplacer `--check` par `--apply`. Ne jamais utiliser `--tls-mode existing` avec un vhost HTTP géré comme substitut silencieux à HTTPS.

Le setup crée `/etc/autokom/secrets/frontend.env` (0600 root) en préservant les autres variables, provisionne un compte Linux **sans privilège** `autokom-frontend-builder`, prépare les répertoires des releases et, en mode géré, écrit le vhost isolé.

## 3. Variables d'environnement

`sudo autokom app frontend env show` affiche la configuration ; `sudo autokom app frontend env edit` permet de la modifier. Fichier : `/etc/autokom/secrets/frontend.env`.

| Variable | Valeur attendue | Rôle |
|---|---|---|
| `FRONTEND_URL` | `https://auto.ik0m.tech` | Domaine public React |
| `BACKEND_URL` | `https://backend.auto.ik0m.tech` | Nom d'hôte de Django |
| `VITE_AUTOKOM_API_BASE` | `/api` | API relative, même origine via Nginx |
| `VITE_AUTOKOM_FRONTEND_ONLY` | `false` | Déclare que le backend existe |
| `VITE_AUTOKOM_API_ENABLED` | `true` | Active les fondations API |
| `VITE_AUTOKOM_GITHUB_ENABLED` | `false` | Reste désactivé tant que GitHubConnector est simulé |

**Interdit dans `VITE_*` :** secrets Django, clés AES, tokens GitHub/Jenkins/Cloudflare, SMTP, mots de passe MySQL. Les variables `VITE_*` sont intégrées dans `dist/assets/*.js` et donc publiques.

La configuration backend **n'est pas déplacée** dans le frontend. Les jetons clients JWT sont passés en Bearer via `src/services/api.js`, stockés uniquement en mémoire. Il n'y a pas encore de persistance/session durable : à traiter à l'intégration des formulaires réels (stratégie BFF ou cookies HttpOnly côté serveur). Aucun faux login n'est transformé en authentification réelle dans ce patch.

## 4. Tests et déploiement

```bash
sudo autokom app frontend deploy --check  # npm ci + tests + npm audit + Semgrep + Trivy + Vite
sudo autokom app frontend deploy --apply  # même contrôles, puis bascule atomique
sudo autokom app frontend status
sudo autokom app frontend logs --source error --lines 50
curl -i https://auto.ik0m.tech/__autokom/health
curl -i https://auto.ik0m.tech/api/health/
```

Le contrôle `--check` crée des fichiers **temporaires de build**, mais ne publie pas de release et ne modifie pas le vhost actif. Le `--apply` installe un artefact versionné puis change `current` par remplacement atomique. Les sorties de `npm audit`, Semgrep et Trivy bloquent le déploiement sur échec. Si les scans réclament une connexion Internet, le réseau sortant du builder doit permettre les endpoints de confiance ; on ne désactive pas les scans pour contourner une panne réseau.

La commande `sudo autokom app frontend status` vérifie la cohérence du symlink et du fichier index, mais ne remplace pas un test public HTTP/TLS ni un smoke test fonctionnel.

Pour revenir à la dernière release connue :

```bash
sudo autokom app frontend rollback --check
sudo autokom app frontend rollback --apply
```

On peut aussi choisir un identifiant de release autorisé via `--to ID`. Le rollback concerne uniquement l'artefact React ; il **n'annule pas** les migrations MySQL ni un déploiement backend.

## 5. Jenkins / DevSecOps

Pipeline défini par `pipelines/autokom-frontend/Jenkinsfile` :
checkout revu → `npm ci --ignore-scripts` → tests → `npm audit` → Semgrep → Trivy → build Vite → contrôle d'artefact → archivage du build.

Ce pipeline **ne publie pas directement** sur `/var/www` et n'a pas de secret privilégié. La mise en production est commandée explicitement par AUTOKOM après approbation. Pour atteindre la provenance complète, ajouter ultérieurement attestations SBOM et signatures des artefacts, ainsi qu'un lien vérifié entre commit CI approuvé et code que `autokom app frontend deploy` reconstruit.

## 6. Frontend actuel / fonctionnalités à connecter ensuite

- `Login.jsx`, `Register.jsx`, `GitHubConnector.jsx`, `Deploy.jsx` : parcours de démonstration inchangé.
- `src/services/api.js` : client JSON central, base `/api`, Bearer JWT non persistant, gestion d'erreurs.
- `src/services/authApi.js` : fonctions disponibles pour `/api/auth/register/`, `/api/auth/login/`, `/api/auth/me/` et `/api/auth/logout-all/`. Les composants ne les appellent pas encore.
- `src/config/githubApi.js` : module de compatibilité ; **ne représente pas** une connexion GitHub achevée.
- `src/config/projectAnalysisApi.js` contient encore des routes `/api/v1` et des endpoints non implémentés par le backend courant ; il n'est pas utilisé dans le parcours actuel. Le migrer lors du raccordement métier, sans inventer d'endpoint.
- `src/services/supabaseClient.js` est un fichier hérité non importé par le bundle actuel ; aucune dépendance Supabase n'a été ajoutée, car l'authentification de référence est le backend Django. Sa migration/suppression est à décider séparément.

## 7. Contrôles utiles avant mise en production

```bash
sudo nginx -t
sudo nginx -T | grep -n -E 'server_name (auto\.ik0m\.tech|backend\.auto\.ik0m\.tech)'
node --version
npm --version
semgrep --version
trivy --version
sudo autokom app frontend status
```

**Points non démontrés localement :** installation npm complète, qualité des scanners sur toutes les dépendances, validité ACME, routage Cloudflare, healthcheck Django distant, cohérence de l'agent Jenkins et cohérence des permissions AWS. Effectuer le pilote avant utilisation client réelle.
