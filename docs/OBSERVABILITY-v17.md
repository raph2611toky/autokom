# Observabilité v17 — audit et corrections du 28 octobre 2026

Base réellement fournie : commit `c21e851` (et non seulement la révision v15 citée dans la conversation précédente).

## Diagnostic

- `rule_files: null` est une valeur YAML vide. L'ancien `setdefault` la conservait, puis refusait de fusionner la liste. Le fichier autokom-alerts.yml pouvait donc exister sans être référencé et chargé par Prometheus.
- Une datasource Grafana ne crée pas de dashboards. Aucun provider ni dashboard n'était fourni.
- Node Exporter collecte l'hôte ; il ne remplace pas un collecteur par conteneur. Aucun job cAdvisor n'était installé.
- Les deux alertes existantes ne détectaient ni saturation des ressources, ni disparition complète du job. Un `up == 0` ne produit rien si la série n'existe pas.
- Si seul le fichier des règles changeait, le rechargement de Prometheus pouvait être omis.
- Le message d'erreur exact évoqué dans l'ancienne conversation n'a pas été récupéré. Ces défauts sont constatés dans le code ; ils ne constituent pas un diagnostic exhaustif de la machine EC2.

## Contenu

Fusion des valeurs nulles de rule_files / alerting / alertmanagers ; conservation des autres jobs, fichiers de règles et destinations. Reconnaissance localhost sans doublon Alertmanager. Validation des règles candidates avec promtool avant écriture et contrôle des noms chargés via API.

Quatre dashboards déclaratifs : Serveur (CPU/RAM/disque/inodes/charge/réseau/uptime), Jenkins (collecte/exécuteurs/file/builds), Docker (CPU/RAM/réseau/disque par conteneur), Collecte et alertes. UID de datasource dynamique, noms de jobs Jenkins configurables. Provider dans `/etc/grafana/provisioning/dashboards/autokom.yaml`, JSON dans `/var/lib/grafana/dashboards/autokom`. Les autres dashboards restent inchangés ; les JSON AUTOKOM sont gérés par code. La vérification API des dashboards nécessite les identifiants Grafana existants ; aucune réinitialisation du mot de passe ajoutée.

Neuf alertes : Jenkins/Node indisponibles ou absents, CPU >90 % pendant 10 min, RAM >90 % pendant 10 min, disque/inodes disponibles <10 % pendant 10 min, erreurs de transmission Prometheus, cAdvisor DOWN, mémoire de conteneur >90 % de sa limite. Les seuils sont une base à adapter à la charge réelle. La limite mémoire doit être définie pour une alerte conteneur utile. Un conteneur supprimé n'est pas détecté individuellement : cela nécessite un inventaire des conteneurs attendus.

cAdvisor est optionnel, proposé pendant la configuration : image officielle épinglée `ghcr.io/google/cadvisor:0.55.1`, conteneur `autokom-cadvisor`, publication `127.0.0.1:8081:8080`, scrape 30 s. Son accès privilégié à l'hôte est annoncé avant activation. Docker n'est pas redémarré, daemon.json n'est pas modifié. Une collision de nom/port ou une publication publique est refusée. Le flux cible Docker rootful Ubuntu, chemin `/var/lib/docker` ; rootless ou data-root personnalisé demandent une adaptation. Certaines métriques de stockage dépendent du runtime.

L'audit `autokom check` distingue fichiers provisionnés, règles chargées/évaluées, cibles UP et séries réellement présentes. Il ne prétend jamais prouver la remise SMTP. L'absence de cAdvisor optionnel reste un avertissement.

## Mise à jour de la source

L'archive contient l'arborescence AUTOKOM sans `.git` ni caches Python. Recopier ses fichiers dans le dépôt existant en préservant `.git` ; examiner `git diff` avant commit/push. Aucun commit, push ou déploiement n'a été réalisé par cette livraison.

Sur EC2, une fois la source corrigée déployée à son emplacement habituel, faire d'abord la simulation :

```bash
sudo autokom install --check
sudo autokom check
```

Après sauvegarde des configurations et pendant une fenêtre de maintenance, forcer la phase de reconfiguration afin d'intégrer les dashboards et règles :

```bash
sudo autokom configure observability
```

Accepter Node Exporter, Alertmanager et, pour les métriques de conteneurs, cAdvisor. Conserver les identifiants actuels. Les redémarrages de Grafana et Alertmanager et le rechargement de Prometheus font partie de cette opération. Le SMTP existant est conservé, notamment Brevo sur le port 587.

Puis :

```bash
sudo autokom check
sudo promtool check config /etc/prometheus/prometheus.yml
sudo promtool check rules /etc/prometheus/rules/autokom-alerts.yml
sudo amtool check-config /etc/prometheus/alertmanager.yml
curl -fsS 'http://127.0.0.1:9090/api/v1/targets'
curl -fsS 'http://127.0.0.1:9090/api/v1/rules?type=alert'
curl -fsS 'http://127.0.0.1:9090/api/v1/alertmanagers'
curl -fsSG 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=node_memory_MemAvailable_bytes{job="node"}'
curl -fsSG 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=container_memory_working_set_bytes{job="cadvisor",image!=""}'
```

Les URLs correspondent aux ports par défaut de cette installation. Adapter en cas de surcharge. Attendre plusieurs scrapes (2–5 minutes pour les courbes `rate`) puis ouvrir Grafana → Dashboards → AUTOKOM. Un panneau sans données ne signifie pas zéro : vérifier sa requête dans Explore et le job/exporteur correspondant. Les panels de builds dépendent de l'activité et des métriques exposées par le plugin Jenkins.

## Test contrôlé de bout en bout (envoi réel d'un e-mail)

À exécuter volontairement sur le serveur, après vérification du destinataire SMTP et des éventuels silences/inhibitions Alertmanager. Ne pas lancer automatiquement dans un test local. Les alertes ici sont gérées par Prometheus/Alertmanager, pas par le moteur Grafana Alerting.

1. Copier `/etc/prometheus/rules/autokom-alerts.yml` vers un fichier de sauvegarde hors du répertoire de règles.
2. Ajouter ce groupe **à la liste `groups` existante**, sans créer une deuxième clé `groups` :

```yaml
  - name: autokom-controlled-test
    rules:
      - alert: AUTOKOMPipelineTest
        expr: vector(1)
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Test volontaire AUTOKOM — aucune panne réelle"
```

3. Valider avec `promtool check rules /etc/prometheus/rules/autokom-alerts.yml`, puis `sudo systemctl reload-or-restart prometheus`.
4. Vérifier `AUTOKOMPipelineTest` dans `/api/v1/rules`, puis `/api/v1/alerts` : pending puis firing. Attendre l'intervalle d'évaluation et le `group_wait` (30 s dans la configuration AUTOKOM).
5. Vérifier sa présence avec `curl -fsS http://127.0.0.1:9093/api/v2/alerts`, puis la réception réelle dans la boîte destinataire (y compris spam).
6. En cas d'absence d'e-mail, consulter `sudo journalctl -u prometheus-alertmanager -n 100 --no-pager` et vérifier le receiver SMTP, STARTTLS 587, l'expéditeur Brevo autorisé et les sorties réseau.
7. Restaurer le fichier sauvegardé, revalider et recharger Prometheus, même si le test échoue. Vérifier que l'alerte disparaît ; un e-mail de résolution peut suivre.

Ce test contrôle la chaîne complète. Un POST direct à Alertmanager ne prouverait pas l'évaluation ni la transmission depuis Prometheus.

## Validation locale et limites

292 tests unittest réussis avec `env -u NO_COLOR TERM=xterm python -m unittest discover -s tests`. 39 expressions PromQL analysées avec promql-parser. 51 fichiers Python compilés et 25 scripts shell validés avec bash -n ; git diff --check sans erreur. Les variables NO_COLOR=1 et TERM=dumb de l'environnement initial faisaient échouer les tests de couleurs/TTY ; aucun changement produit n'a été ajouté pour masquer cela.

Ni Docker, ni systemd, ni Grafana/Prometheus ne tournent dans l'environnement de validation. Les appels d'installation et les APIs sont simulés dans les tests. promtool/amtool, la version cAdvisor sur EC2, le rendu Grafana et l'acheminement Brevo restent à vérifier sur le serveur avec les commandes ci-dessus. Aucun accès à l'EC2 ni e-mail envoyé.

Références :
- https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- https://grafana.com/docs/grafana/latest/administration/provisioning/
- https://prometheus.io/docs/guides/cadvisor/
- https://github.com/google/cadvisor
- https://github.com/jenkinsci/prometheus-plugin/blob/master/docs/metrics/index.md
