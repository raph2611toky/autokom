# AUTOKOM — intégration fonctionnelle GCP HA, étape 2A

## Ce qui est livré

- Le choix de haute disponibilité (`high_availability_requested`) est conservé dans les **versions Django** des configurations individuelles et dans les projets `ServiceStack` frontend + backend.
- Une migration Django `configurations.0005` ajoute les deux colonnes, par défaut `false`, sans modifier les anciennes publications.
- L'API renvoie le choix et refuse d'en faire une autorisation commerciale : les droits `ApplicationEntitlement` et `HighAvailabilityOption` sont contrôlés côté serveur.
- Tant que les workers GCP et le routage applicatif ne sont pas prêts, la plateforme **n'exécute pas une réplique locale à la place d'une HA demandée**.
- `autokom_ha_placement.py` produit des **candidats de placement en lecture seule** : une VM pour le standard et deux VM dans des zones distinctes pour la HA.
- Exporteur de métriques déjà existant : unité systemd optionnelle et exemple de job Prometheus fournis.

## Ce que cette archive NE fait PAS

- Elle ne transforme PAS le template MIG de validation en runtime applicatif.
- Elle ne publie PAS les images Docker sur les workers GCP.
- Elle ne modifie PAS les règles de domaine, certificats, NEGs, URL maps, ou le Load Balancer.
- Elle ne garantit PAS la reprise automatique d'une application sur remplacement d'une VM.
- Elle ne change PAS les ressources Terraform ni le serveur temporaire du stagiaire.

### Pourquoi on conserve ce verrou

Le MIG existant est basé sur une VM `e2-small` sans adresse IP publique, et le sous-réseau GCP n'autorise pas encore Private Google Access. L'agent de déploiement, la livraison d'images, la configuration des secrets, le routage par domaine et le contrôle de santé propre à chaque application doivent être opérationnels **avant** l'activation des demandes HA. Pour les applications avec état (session, fichiers, SQLite), la persistance doit être externalisée.

## Mise à jour

Depuis Kali, intégrer les fichiers complets dans le dépôt et pousser. Sur Ubuntu, après sauvegarde et hors publication :

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
```

La migration Django `configurations.0005` doit être appliquée par `autokom app deploy` avant l'utilisation de l'UI actualisée.

## Inspecter un placement candidat sans rien créer

```bash
python3 /opt/autokom/source/scripts/ha/autokom_ha_placement.py \
  --environment 3a741822-18dd-4888-9a1c-e89886ddc6af \
  --mode ha --format json
```

Le code de sortie `3` est **attendu** tant que `can_host_customer_applications` reste faux ; il indique une sélection possible mais pas une livraison activable. Ce script ne fait que des lectures GCP.

## Exporteur Prometheus permanent (optionnel)

Faire tourner l'exporteur sous une identité systemd dynamique (pas de clé JSON). L'identité GCP est fournie par le serveur Compute Engine. À vérifier après installation :

```bash
sudo install -m 0644 \
  /opt/autokom/source/scripts/ha/systemd/autokom-ha-metrics.service \
  /etc/systemd/system/autokom-ha-metrics.service
sudo systemctl daemon-reload
sudo systemctl enable --now autokom-ha-metrics.service
sudo systemctl is-active autokom-ha-metrics.service
curl -fsS http://127.0.0.1:19191/metrics
```

Si `autokom_ha_metrics_scrape_success` vaut `0`, consulter :

```bash
sudo journalctl -u autokom-ha-metrics.service -n 100 --no-pager
```

Ne pas annoncer la supervision Cloud Monitoring comme fonctionnelle sans vérification de cette métrique. Pour Prometheus, ajouter **une seule fois** le job du fichier `ha-prometheus-scrape.yml` sous `scrape_configs:`, exécuter `promtool check config`, puis recharger Prometheus avec la méthode configurée sur ce serveur.

## Conditions du prochain basculement vers le runtime client

1. Accès privé Google APIs ou Cloud NAT sur le réseau des workers ; prévoir le coût du NAT si choisi.
2. Template worker immuable avec Docker/agent, processus systemd de réconciliation et bootstrap reproductible pour toute VM créée par l'Autoscaler.
3. Distribution d'images versionnées et contrôlées, secrets hors métadonnées Terraform et bucket d'état Terraform.
4. Routage par hôte avec backends/NEGs contrôlés par une seule source de vérité ; ne pas modifier une URL map gérée par Terraform par des commandes non suivies.
5. Contrôle de santé *par application*, déploiement progressif, rollback, limites de mémoire des e2-small et politiques de stockage externe.
6. Tests réels : standard = 1 service sur 1 worker, HA = 2 réplicas sur 2 zones ; arrêt d'une VM ; recréation automatique ; redéploiement après scale-out.
7. Mise en service DNS/HTTPS des clients vers le LB uniquement après vérification, jamais avant.

Les anciennes applications restent sur l'orchestrateur tant que ces critères ne sont pas satisfaits. Aucune application cliente ne doit être déplacée par un simple changement de la case HA.
