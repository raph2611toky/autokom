# AUTOKOM — MIG et LB : préflight (lecture seule)

`autokom_ha_preflight.py` contrôle les **ressources GCP déjà existantes** : MIG régional,
instances, santé des backends, template du MIG et règles du Load Balancer.

```bash
python3 scripts/ha/autokom_ha_preflight.py
python3 scripts/ha/autokom_ha_preflight.py --with-metrics
python3 scripts/ha/autokom_ha_preflight.py --with-metrics --format=json
python3 scripts/ha/autokom_ha_preflight.py --with-metrics --format=prometheus
```

Le contrôle `validation_server.py` de la configuration Terraform initiale bloque
explicitement la prétention que les applications clientes sont actuellement gérées par le MIG.
Le script n'effectue aucune modification GCP, ni sur des VMs, ni sur Terraform.

La métrique Google Cloud Monitoring utilisée pour le Load Balancer **global externe**
est `loadbalancing.googleapis.com/https/request_count` filtrée sur
`resource.type="https_lb_rule"` et `resource.labels.url_map_name`.
Le calcul distingue les codes 2xx, 3xx, 4xx, 5xx sur une fenêtre de 5 minutes.
Un retour `monitoring_status=unavailable` signale que l'API ou
`roles/monitoring.viewer` peut manquer, sans toucher aux permissions.

## Suite d'intégration applicative — contrôles préalables obligatoires

1. Image de base MIG capable de servir les applications clientes et de se reconstituer
   automatiquement (image immutable ou agent de pull par identité GCP).
2. Distribution sécurisée d'images, configs et secrets, sans clés JSON permanentes.
3. Routage à l'application (host header) et santé de **chaque** application.
4. Plan de contrôle Django versionné : `ha_enabled`, cible d'hébergement,
   déploiement un ou deux workers, droit d'offre et migration des modes.
5. Survivabilité à la perte d'une VM puis remplacement par Autoscaler
   (nouvelle VM reçoit les applications prévues automatiquement).
6. DNS et HTTPS des applications clientes dirigés vers le LB global, et non le
   serveur orchestrateur, si l'objectif est une HA de bout en bout.
7. Base de données et sessions hors des VM stateless, avec sauvegardes.

**Attention :** activer un simple booléen dans React ne ferait pas fonctionner
l'HA tant que ces prérequis ne sont pas mis en œuvre.

## Exporteur Prometheus local (optionnel, non installé par défaut)

```bash
python3 scripts/ha/autokom_ha_metrics_server.py --port 19191
# autre terminal
curl -fsS http://127.0.0.1:19191/metrics
```

Dans le fichier Prometheus existant, **après sauvegarde et validation du YAML**,
ajouter le job suivant à `scrape_configs` (ne pas recréer la section) :

```yaml
- job_name: 'autokom-gcp-ha'
  scrape_interval: 60s
  static_configs:
    - targets: ['127.0.0.1:19191']
```

Exposer cet exporteur sur l'IP publique ou sans authentification est interdit.
Ne pas créer de règle de pare-feu GCP pour le port 19191.
Son exécution permanente nécessite un service systemd durci testé sur Ubuntu,
qui n'est **pas** installé par cette archive.

## Dashboard Grafana

Importer `autokom-ha-grafana-dashboard.json` dans Grafana et associer la source
Prometheus existante. Le tableau affiche le nombre de VM, la santé des
backends, le trafic et les erreurs 5xx sur cinq minutes. Il **ne** prouve pas
une HA applicative tant que le template MIG sert le mode validation.
