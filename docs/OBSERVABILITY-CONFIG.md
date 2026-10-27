> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Observabilité configurable (v2)

## Objectif

Une seule configuration **non sensible** fait foi. Les ports, URLs et chemins Prometheus/Grafana/Jenkins sont dérivés du même fichier, sans éditer les scripts Python, les services systemd ou les blocs Nginx à la main. La configuration est validée avant toute opération. Les secrets **ne vont jamais dans les fichiers INI** et restent gérés par les invites sécurisées du module d'installation.

### Hiérarchie (du moins prioritaire au plus prioritaire)

1. `config/observability/defaults.ini` versionné dans le dépôt AUTOKOM.
2. `/etc/autokom/observability.ini` local, non versionné, facultatif.
3. Fichier explicitement donné avec `--config /chemin/installation.ini`, ou `AUTOKOM_OBSERVABILITY_CONFIG`.
4. Paramètres `--set section.option=valeur`, répétables. Ne persistent **pas** entre deux exécutions.
5. Options de domaine historiques `--prometheus-domain`, `--grafana-domain`, `--email`, et questions interactives.

**Ne pas modifier `defaults.ini` pour chaque serveur** ; préférer `/etc/autokom/observability.ini` (hors Git).

## Exemple de configuration locale

```ini
[jenkins]
port = 8080
metrics_path = /prometheus/
job_name = jenkins

[prometheus]
port = 9091
scrape_interval = 20s
retention = 10d
storage_path = /var/lib/prometheus/metrics2

[grafana]
port = 3001
datasource_name = AUTOKOM Prometheus
datasource_uid = autokom-prometheus

[domains]
prometheus =
grafana = grafana.example.com
letsencrypt_email = devops@example.com
```

Les hôtes `jenkins.host`, `prometheus.host`, `grafana.host` valent obligatoirement `127.0.0.1` pour cette version : **pas d'écoute publique**. Les domaines restent facultatifs. Un domaine implique l'accès HTTPS via Nginx ; les ports directs restent internes.

## Commandes

Dans le dépôt sur Kali ou Ubuntu, sans sudo :

```bash
python3 scripts/lib/observability.py --show-config
python3 scripts/lib/observability.py --show-config \
  --set jenkins.port=8081 --set prometheus.port=9091 --set grafana.port=3001
```

Sur le serveur :

```bash
sudo install -d -m 755 /etc/autokom
sudoedit /etc/autokom/observability.ini
sudo bash scripts/install/observability.sh --check --interactive
# Une fois vérifié, seulement si nécessaire :
sudo bash scripts/install/observability.sh --apply --interactive
```

Pour un autre fichier de configuration :

```bash
sudo bash scripts/install/observability.sh --check --non-interactive \
  --config /etc/autokom/environments/staging.ini
```

Ne jamais stocker les mots de passe Grafana, les tokens Jenkins ni les credentials HTTP BasicAuth dans les fichiers INI ou les commandes `--set`. Utiliser les invites masquées déjà prévues.

## Changements propagés automatiquement

- `jenkins.port` : URL locale Jenkins, cible scrape Prometheus, vérification du port, restriction systemd Jenkins **uniquement si la valeur correspond à son port actif**. Un déplacement du port Jenkins sur une instance en cours d'utilisation n'est jamais effectué implicitement ; adapter le module Jenkins et ses proxies d'abord.
- `jenkins.metrics_path` : HTTP exporter, metrics_path Prometheus et restriction Nginx du domaine Jenkins. Le chemin doit réellement être servi par le plugin Jenkins (le changer ici ne reconfigure pas magiquement le plugin).
- `prometheus.port` : service systemd, self-scrape, URL datasource Grafana, API de vérification, proxy Nginx AUTOKOM et tunnel SSH proposé.
- `grafana.port` : service systemd, API locale et proxy Nginx AUTOKOM, tunnel SSH proposé.
- `scrape_interval`, `retention`, `storage_path` : configurations Prometheus et datasource. **Un déplacement de `storage_path` est refusé automatiquement sur une instance existante**, car la migration des séries temporelles doit être préparée et vérifiée manuellement.
- `domains.prometheus`, `domains.grafana` : Nginx/TLS ; les virtual hosts non gérés par AUTOKOM ne sont jamais réécrits.
- `grafana.datasource_uid` : identifiant stable; **ne pas changer après le premier déploiement** sans migration, sinon une nouvelle source pourrait être créée.

Lors de la première installation réussie, AUTOKOM stocke sous `/etc/autokom/state/observability.json` un petit état **sans secrets**, permettant de reconnaître les anciens ports et de migrer ses propres jobs de collecte, sans supprimer les autres jobs. Le programme vérifie l'endpoint Jenkins et la cible Prometheus avant d'enregistrer cet état.

## Ports HTTPS et sécurité

- Les ports HTTPS Nginx `80/443` ne sont volontairement pas paramétrables dans cette phase ; ils sont les points d'entrée standard de Certbot HTTP-01 et du reverse proxy public.
- Les accès privés `127.0.0.1` ne doivent jamais être confondus avec une publication Docker : vérifier les règles UFW, Security Groups AWS et les éventuelles publications des conteneurs.
- Une URL locale est reconstruite depuis le port configuré : seules les requêtes vers `127.0.0.1` et les trois ports déclarés sont autorisées pour les appels administratifs internes.
- Les certificats existants sont réutilisés s'ils sont valides. Un vhost déjà utilisé par un autre projet n'est jamais écrasé.
- Avant toute modification de port sur un serveur actif, vérifier les autres applications et dépendances, puis sauvegarder les configurations et superviser les redémarrages.

## Validation

```bash
python3 -m py_compile scripts/lib/observability.py scripts/lib/observability_config.py
python3 tests/test_observability.py
python3 tests/test_observability_config.py
bash -n scripts/install/observability.sh
```

Ces tests sont locaux et simulés ; ils ne remplacent pas un `--check` sur le serveur réel, les contrôles Nginx/Certbot, la vérification Jenkins `/prometheus/`, le statut Prometheus `UP` et la disponibilité Grafana.
