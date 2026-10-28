> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Phase 3 : Jenkins + Prometheus + Grafana

## Installer

Module indépendant, sans remplacement du `install.sh` existant.

```bash
# Sur la copie Git du serveur Ubuntu (après avoir poussé les fichiers)
sudo git -C /opt/autokom/source pull --ff-only
sudo bash /opt/autokom/source/scripts/install/observability.sh --check --interactive
# Ensuite, après inspection et sauvegardes :
sudo bash /opt/autokom/source/scripts/install/observability.sh --apply --interactive
```

Pour désigner les domaines sans les redemander :

```bash
sudo bash /opt/autokom/source/scripts/install/observability.sh --check \
  --prometheus-domain metrics.example.com \
  --grafana-domain grafana.example.com
```

Les secrets ne sont jamais acceptés en paramètres de ligne de commande. L'option `--non-interactive` sert aux simulations ou aux installations sans nouvelle authentification à créer ; elle refuse une opération exigeant un nouveau mot de passe.

## Ordre des opérations

1. Détecter Jenkins et ses métriques `/prometheus/` en local.
2. Vérifier que l'accès direct Jenkins 8080 n'est pas public ; corriger uniquement avec confirmation si nécessaire.
3. Repérer les domaines Nginx de Jenkins et refuser l'accès public à `/prometheus/` (403), avec sauvegarde et `nginx -t` ; Prometheus collecte **directement** sur `127.0.0.1:8080`.
4. Installer/activer le plugin `prometheus` via le Jenkins CLI si absent (demande le token de l'admin via le module Jenkins existant, pas de token permanent ajouté pour cette opération). Lorsque le plugin exige l'authentification `Metrics.VIEW`, le module demande un token dédié à la lecture des métriques et le range dans `/etc/prometheus/autokom-jenkins-token`, `root:prometheus`, `0640`.
5. Installer Prometheus à partir des paquets Ubuntu (Ubuntu 26.04 paquet `prometheus`). Son service utilise `127.0.0.1:9090`, sa TSDB `/var/lib/prometheus/metrics2` et une rétention 15 jours.
6. Ajouter le job de collecte `jenkins` à `/etc/prometheus/prometheus.yml`, sans supprimer les jobs existants ; vérifier avec `promtool check config` et contrôle `/-/ready`.
7. Installer Grafana OSS via APT signé par `https://apt.grafana.com`; service restreint à `127.0.0.1:3000`. Provisionner `/etc/grafana/provisioning/datasources/autokom-prometheus.yaml` → `http://127.0.0.1:9090`.
8. Sur installation Grafana neuve, demander le nom et le mot de passe admin par TTY, masquer la saisie, changer le mot de passe initial via `PUT /api/user/password` en local, vérifier. Sur Grafana existant, conserver les comptes ou demander l'ancien mot de passe en cas de changement volontaire.
9. Domaines facultatifs : si présent, vérifier le DNS et collisions de Nginx, obtenir un certificat via `certbot certonly --nginx`; publier ensuite **en HTTPS seulement**. Prometheus reçoit BasicAuth SHA-512 crypt en Nginx, Grafana utilise son login natif. Un site provisoire retourne `503` durant l'émission du certificat. En cas d'échec, retour au portail `503`, jamais à un reverse proxy HTTP non chiffré.
10. Vérifier la cible Jenkins `UP` via `/api/v1/targets`, puis valider les ports/URLs.

## Domaines facultatifs

Sans domaine : le navigateur accède aux interfaces via tunnel SSH, par exemple :

```bash
ssh -L 9090:127.0.0.1:9090 -L 3000:127.0.0.1:3000 ubuntu@IP_OU_HOST_PUBLIC
```

Après connexion : `http://127.0.0.1:9090` et `http://127.0.0.1:3000`.

Avec domaine : seules les connexions entrantes HTTPS vers Nginx (443) sont publiques. Prometheus HTTP BasicAuth requiert un compte et un mot de passe ; Grafana conserve son propre login.

### Remarques sur l'authentification

- **Prometheus n'est pas un système multi-utilisateurs** : le mode sans domaine est interne uniquement ; avec domaine, la sécurité est assurée par `auth_basic` en Nginx, hash SHA-512 crypt via OpenSSL stocké `/etc/nginx/.autokom-prometheus.htpasswd` (`root:www-data`, 0640).
- **Grafana** possède des comptes natifs. Ne jamais conserver de mot de passe en clair dans Git ou un argument CLI. AUTOKOM modifie les credentials existants uniquement si demandé.
- **Jenkins** : le plugin Prometheus ne nécessite normalement pas d'authentification, mais un réglage Jenkins peut imposer `Metrics.VIEW`. Utiliser alors un utilisateur et token **lecture métriques**, pas un token administrateur ; le scraper local utilise `basic_auth.password_file`.
- La route publique `/prometheus/` de Jenkins est bloquée dans le serveur Nginx Jenkins ; une connexion directe sur le port Jenkins hors localhost serait une faille, d'où la restriction loopback.

## Protection des installations existantes

- Les fichiers `*.autokom.bak` sont créés avant les écritures importantes.
- Un vhost d'un autre service ne sera pas remplacé. Un certificat existant validé sera réutilisé.
- Un Prometheus/Grafana existant peut avoir des paramètres de données personnalisés ; si la configuration n'est pas reconnue, le script **s'arrête** plutôt que d'écraser la TSDB, la base Grafana ou les utilisateurs.
- Confirmer explicitement les redémarrages des services déjà actifs lorsqu'une correction les nécessite.
- Sans domaine, aucun port 9090/3000 n'est ouvert en dehors de localhost.
- Aucun UFW/Security Group AWS n'est automatiquement ouvert ou désactivé. Vérifier AWS SG pour 3000/9090/8080, 3306 et 33060.

## Vérifications sur le serveur

```bash
sudo ss -lntp | grep -E ':(8080|9090|3000)\\b'
sudo nginx -t
sudo promtool check config /etc/prometheus/prometheus.yml
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool
curl -sSI http://127.0.0.1:3000/login
sudo systemctl status prometheus grafana-server --no-pager
```

Prometheus : `Status → Targets`, cible `jenkins` UP. Grafana : `Connections → Data Sources`, source `AUTOKOM Prometheus`.

## Limites et prérequis

- Périmètre : Ubuntu avec paquets APT et systemd ; pas de support Dockerisé pour Prometheus/Grafana dans ce module. Pour une instance existante fortement personnalisée, analyse et adaptation du déploiement requises.
- Les tests locaux n'exécutent ni APT, ni une instance réelle Jenkins/Grafana, ni un ACME HTTP-01 ; les vérifier sur AWS en commençant par `--check`.
- L'installateur exige `nginx_discovery.py` et `jenkins_postinstall.py`, déjà présents dans les phases 2/2B.
- Les versions des plugins et dépendances sont résolues par le système APT/Jenkins ; leurs compatibilités avec la version Jenkins installée doivent être validées sur le serveur.

## Références

- Jenkins Prometheus plugin : https://plugins.jenkins.io/prometheus/
- Prometheus scrape / configuration : https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Grafana APT : https://grafana.com/docs/grafana/latest/setup-grafana/installation/debian/
- Grafana provisioning : https://grafana.com/docs/grafana/latest/administration/provisioning/
- Grafana reverse proxy : https://grafana.com/tutorials/run-grafana-behind-a-proxy/
- Certbot Nginx : https://eff-certbot.readthedocs.io/en/stable/using.html#nginx
