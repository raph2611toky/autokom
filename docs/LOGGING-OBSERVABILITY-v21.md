# AUTOKOM v21 — exploitation Loki + Alloy, Prometheus/Grafana et sonde E2E

## Base, sécurité et compatibilité

Correctif **différentiel** sur AUTOKOM v20. Il ne change pas l'installation Jenkins, les dashboards Serveur/Docker/Jenkins/Collecte existants, les routages SMTP Alertmanager, les identifiants, l'instance EC2 ni le mode dual backend OpenSearch. Les configs personnalisées détectées ne sont pas écrasées. Garder un snapshot/sauvegarde EC2/EBS et du dossier `/etc/autokom`, du provisionning Grafana et de `/etc/prometheus` avant migration.

**IMPORTANT :** la suite de tests Python hors ligne valide le générateur et ses API, mais ni l'exécution Alloy/Loki ni le fonctionnement des alertes et de Grafana sur la véritable EC2. Les commands de validation Docker et de sonde E2E sur la machine sont obligatoires. En cas d'erreur de validation, la procédure n'annonce pas de succès.

## Chaîne de collecte

`Docker (services et stdout)` + `journald (Jenkins, sshd, services Linux)` + `/var/log/nginx/*.log` → **Alloy** → nettoyage best effort et extraction JSON (`level` + `trace_id`/`request_id` en structured metadata) → Loki (ou OpenSearch/Data Prepper selon backend déjà installé). Labels principaux : `platform="autokom"`, `source="docker|systemd|nginx"`, `service_name`, plus `unit`, `container`, `compose_service`, `level` lorsqu'ils existent. Ne pas indexer d'IP, requête, identifiant utilisateur ou trace. Les secrets présents dans les logs ne sont pas entièrement garantis masqués par des expressions génériques : production = éviter à la source d'émettre secrets et données personnelles.

Alloy rend ses métriques accessibles **uniquement par publication sur 127.0.0.1:12345**, Loki sur `127.0.0.1:3100`. Le Grafana système consomme sa datasource Loki sur localhost. Aucun port n'est ajouté aux Security Groups AWS. Les conteneurs de journalisation eux-mêmes continuent d'être exclus de la collecte Docker pour éviter une boucle d'ingestion.

La connexion au socket Docker reste **privilégiée malgré le montage `:ro`** ; durcissement futur : socket-proxy à ACL strictes ou collecteur hôte non privilégié. Loki sans authentification HTTP n'est pas un service public. Rétention locale : **7 jours**, sans garantie d'espace disque si le volume se remplit avant échéance. Prendre en compte le volume EBS, backups, et règle d'alerte disque.

## Nouvelles commandes

```bash
autokom logs status                         # état détaillé, lecture seule
sudo autokom logs reconcile --check         # revue, aucune mutation
sudo autokom logs reconcile --apply         # réapplique la config du moteur EXISTANT, sans installer OpenSearch
sudo autokom logs verify                    # sonde journald → Alloy → Loki (écriture d'un événement de test)
autokom install logs --check                # comportement de v20 conservé
```

**Mise à jour sur serveur déjà installé :** une installation `autokom install logs` pourra ignorer le module si les conteneurs sont déjà actifs. Utiliser `sudo autokom logs reconcile --apply` **après déploiement du code v21**. La réconciliation détermine le moteur actuel depuis `config.alloy` ; elle ne bascule pas automatiquement vers `both`.

Avant la mise à jour :

```bash
cd /opt/autokom/source
sudo tar czf /root/autokom-pre-v21-$(date +%Y%m%d-%H%M%S).tgz \
  /etc/autokom/logs /etc/prometheus /etc/grafana/provisioning \
  /var/lib/grafana/dashboards/autokom 2>/dev/null
# Inspecter le patch : git apply --check /tmp/AUTOKOM-v21-Logging-Observability.patch
# Ne pas appliquer ce patch si le dépôt a divergé ; fusionner les différences.
```

L'installateur de logs valide **Alloy via `docker run --network none grafana/alloy:v1.11.0 validate ...`** avant d'écraser les fichiers gérés, puis Docker Compose, et enfin le `promtool check rules/config`. L'intégration Prometheus ajoute seulement les jobs `autokom-loki`, `autokom-alloy` et `autokom-logs-canary` et un fichier de règles distinct `/etc/prometheus/rules/autokom-logging.yml`. Le rechargement utilise `SIGHUP` pour éviter de redémarrer Prometheus. Un fichier de sauvegarde unique est créé pour la configuration Prometheus et une tentative de rollback est faite en cas d'échec du rechargement.

La sonde E2E est un timer systemd (`autokom-logs-canary.timer`) exécuté toutes les 5 minutes. Elle émet un marqueur unique dans journald, puis cherche **le même marqueur** dans Loki : son résultat s'expose sur `127.0.0.1:19109/metrics` via `autokom-logs-exporter.service`, qui ne lit pas les journaux eux-mêmes. Le job `autokom-logs-canary` de Prometheus le collecte. La première mesure ne peut être « verte » avant un test réussi.

## Dashboards Grafana (dossier AUTOKOM)

1. `AUTOKOM · Journaux` : logs, filtres source/service, débits, anomalies.
2. `AUTOKOM · Santé des logs` : cibles Loki/Alloy/sonde, échec E2E, ancienneté, latence, logs rejetés, CPU/RAM.
3. `AUTOKOM · Sécurité des journaux` : SSH, authentification, HTTP 4xx/5xx, logs Nginx (détection heuristique).
4. `AUTOKOM · CI/CD et services` : journald Jenkins, Docker Jenkins, logs d'exceptions Docker.

Datasource provisionnée : `AUTOKOM Loki` (UID `autokom-loki`). Les dashboards existants (Serveur, Docker, Jenkins, Collecte et alertes) restent intacts. La migration de `autokom-logs.json` refuse les modifications opérateur non connues, enregistre une sauvegarde `autokom-logs.v20-backup`, puis écrit les signatures de fichiers dans `/etc/autokom/logs/.grafana-managed.json`. **Le provisioning datasource Grafana est chargé au démarrage**. Si `AUTOKOM Loki` est absent dans Connections → Data sources, tester `sudo systemctl restart grafana-server` pendant une fenêtre contrôlée et inspecter les logs Grafana ; ne pas redémarrer tous les composants sans diagnostic.

## Validation fonctionnelle après réconciliation

```bash
sudo docker compose -f /etc/autokom/logs/compose.yaml --env-file /etc/autokom/logs/.env --profile loki ps
curl -fsS http://127.0.0.1:3100/ready
curl -fsS http://127.0.0.1:12345/metrics | head
curl -fsS http://127.0.0.1:19109/metrics
sudo autokom logs verify
sudo autokom logs status
sudo systemctl status autokom-logs-canary.timer autokom-logs-exporter.service --no-pager
curl -fsS 'http://127.0.0.1:9090/api/v1/targets' | python3 -m json.tool
sudo journalctl -u autokom-logs-canary.service -n 40 --no-pager
```

Vérifier **dans Grafana → Explore** la datasource `AUTOKOM Loki`, la période 24 h, puis la requête `{platform="autokom"}`, et chercher `AUTOKOM_CANARY_`. En parallèle, dans Prometheus → Targets, vérifier que les 3 jobs sont `UP`. Dans Alerting/Prometheus, vérifier les règles `AUTOKOMLokiUnavailable`, `AUTOKOMAlloyUnavailable`, `AUTOKOMLogsCanaryUnavailable`, `AUTOKOMLogsEndToEndFailed`, `AUTOKOMLogsCanaryStale`, `AUTOKOMLokiIngestionRejected`, `AUTOKOMLoggingDiskPressure`, puis effectuer un test de livraison via Alertmanager vers la destination réellement configurée. Sans cette dernière validation, **la remise de notifications n'est pas attestée**.

Si le canari échoue malgré `/ready`, rechercher permissions journald et montage journald : `sudo docker compose ... logs --tail=100 alloy`, et inspecter `http://127.0.0.1:12345` via tunnel SSH (`ssh -L 12345:127.0.0.1:12345 ...`). Le socket Docker ne doit jamais être exposé sur le WAN.

## Limites et actions futures

Sur une seule EC2, Loki ne possède **aucune haute disponibilité**. Les snapshots/backups EBS et le test de restauration ne sont pas implémentés dans ce correctif. Les métriques « journaux reçus/rejetés » dépendent des noms exposés par la version Loki et doivent être contrôlées dans `/metrics` pendant la recette. Les alertes heuristiques ne remplacent pas un SIEM ; la cyber-résilience automatique doit attendre des règles validées, contrôles RBAC et procédures de confinement réversibles.
