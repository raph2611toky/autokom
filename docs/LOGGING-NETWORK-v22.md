# AUTOKOM v22 — correctif réseau Loki/Alloy + inventaire d'exploitation

## Symptômes et diagnostic

Sur AWS Ubuntu 26.04, Prometheus signale `autokom-loki` et `autokom-alloy` DOWN avec `connect: connection refused`. `autokom-logs-canary` peut rester UP : cela signifie seulement que **son exporter** sur `19109` répond, pas que le log canari a traversé Alloy et Loki. `autokom tools show` v21 indique `active` parce que Docker déclare ses conteneurs `running`. Ce statut ne démontre ni port publié ni endpoint HTTP prêt.

Le générateur Compose v21 publie bien `127.0.0.1:3100:3100` et `127.0.0.1:12345:12345`, mais attache les conteneurs **uniquement à un réseau `internal: true`**. Docker Engine 29.x peut accepter `ports:` tout en retournant `null` dans `.NetworkSettings.Ports` sur un bridge exclusivement interne ; un incident est documenté sur Ubuntu 26.04. Le fait que `ss` n'affiche rien et que `curl` reçoive `connection refused` est **compatible** avec ce comportement, mais la validation du `docker inspect` de la machine est nécessaire pour le confirmer.

## Architecture réseau corrigée

- Réseau `logging` **interne conservé** pour Loki/Alloy/OpenSearch/Data Prepper : communication inter-conteneurs.
- Réseau `loopback` (bridge standard) attaché uniquement aux conteneurs ayant des ports `ports:` publiés : Loki, Alloy, OpenSearch et OpenSearch Dashboards. Data Prepper reste sur le réseau interne.
- Publications Docker explicitement liées à `127.0.0.1`: Loki `3100`, Alloy `12345`, OpenSearch `9200`, Dashboards `5601` (si installés). Aucun port WAN ajouté.
- Le bridge standard rend néanmoins possible **l'egress** des conteneurs concernés : c'est une modification de la surface réseau par rapport à v21. Elle doit être prise en compte dans une politique d'egress restrictive, notamment dans les environnements de production. `:ro` sur le socket Docker n'annule **pas** ses privilèges.
- Les données persistantes (`loki-data`, `alloy-data`, `opensearch-data`) ne sont ni supprimées, ni reformatées. **Ne jamais exécuter `docker compose down -v`** pour ce correctif.

Sources : [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/), [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/), [incident Docker 29.x internal bridge](https://github.com/moby/moby/discussions/53256), [Alloy Docker /metrics](https://grafana.com/docs/alloy/latest/set-up/install/docker/).

## Préflight — en lecture seule, avant toute modification

```bash
cd /opt/autokom/source
git status --short
sudo docker compose -f /etc/autokom/logs/compose.yaml --env-file /etc/autokom/logs/.env --profile loki ps
sudo docker ps -a --filter label=com.docker.compose.project=autokom-logs --format 'table {{.Names}}\t{{.State}}\t{{.Ports}}'
sudo docker ps -q --filter label=com.docker.compose.project=autokom-logs --filter label=com.docker.compose.service=loki | xargs -r sudo docker inspect --format '{{json .NetworkSettings.Ports}}'
sudo docker ps -q --filter label=com.docker.compose.project=autokom-logs --filter label=com.docker.compose.service=alloy | xargs -r sudo docker inspect --format '{{json .NetworkSettings.Ports}}'
curl -fsS --max-time 4 http://127.0.0.1:3100/ready
curl -fsS --max-time 4 http://127.0.0.1:12345/-/ready
```

Si `Ports` montre `null`, la publication host n'est pas réalisée même si `HostConfig.PortBindings` a enregistré la demande. Si les conteneurs sont `exited`, examiner aussi `docker compose logs` avant toute correction réseau.

## Déploiement sans écraser les configurations opérateur

Conserver un snapshot EBS ainsi qu'une copie locale protégée du répertoire de logs (`.env` contient des secrets OpenSearch). **Ne pas publier la sauvegarde.**

```bash
sudo tar -C /etc/autokom -czf /root/autokom-logs-before-v22.tgz logs
sudo chmod 600 /root/autokom-logs-before-v22.tgz
cd /opt/autokom/source
git apply --check /tmp/AUTOKOM-v22-Logging-Network-Health.patch
git apply /tmp/AUTOKOM-v22-Logging-Network-Health.patch
sudo autokom logs reconcile --check
sudo autokom logs reconcile --apply
```

La réconciliation préserve la préférence `loki` et ne force pas OpenSearch sur une EC2 de 7,6 Gio. Elle validera la configuration Alloy, adaptera la composition Docker, puis **peut recréer Loki et Alloy** pour appliquer leurs nouveaux réseaux. Prévoir une interruption temporaire des logs, mais aucune modification de Jenkins/Grafana/Prometheus n'est requise pour ce correctif réseau. Les vérifications d'API doivent maintenant être positives : sinon la commande renvoie une erreur explicite, pas un faux succès.

En cas d'échec dû à un fichier de configuration modifié manuellement, **ne pas forcer** le remplacement. Fusionner le changement après audit.

## Contrôles après réconciliation

```bash
sudo autokom logs doctor
autokom tools show loki
autokom tools show alloy
sudo docker compose -f /etc/autokom/logs/compose.yaml --env-file /etc/autokom/logs/.env --profile loki ps
curl -fsS --max-time 5 http://127.0.0.1:3100/ready
curl -fsS --max-time 5 http://127.0.0.1:3100/metrics | head -8
curl -fsS --max-time 5 http://127.0.0.1:12345/-/ready
curl -fsS --max-time 5 http://127.0.0.1:12345/metrics | head -8
sudo autokom logs verify
```

Dans **Prometheus → Targets** : `autokom-loki` et `autokom-alloy` **UP** ; canary **UP** ne suffit pas, vérifier `autokom_logs_canary_success == 1`. Dans **Grafana Explore → AUTOKOM Loki**, requête `{platform="autokom"}` sur 24 h ; rechercher l'événement `AUTOKOM_CANARY_...`.

## Consignes de retour arrière

Ne pas supprimer de volumes. Pour retrouver les ports précédents, un retour v21 reproduirait la panne ; privilégier les logs/erreurs plutôt qu'un retour aveugle. Si nécessaire, restaurer les fichiers sauvegardés **après avoir contrôlé leur contenu**, refaire `docker compose config -q`, puis remettre uniquement les services logs en place avec le même profil ; faire valider l'impact réseau. Les alertes historiques et dashboards existants ne sont pas supprimés.

## Limitations de la recette

Tests Python, génération YAML, diff Git vérifiés hors-ligne ; **aucun daemon Docker EC2, socket réel, Prometheus ni Grafana de l'utilisateur n'est accessible depuis la fabrication du patch**. La correction cible la panne de publication des ports, pas toutes les causes possibles de logs absents (permissions journald, erreurs Alloy, certif TLS OpenSearch, Grafana datasource, etc.).
