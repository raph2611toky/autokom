# AUTOKOM v23 — Choix explicite de l'exécution Loki + Alloy

## Principe

L'administrateur DevSecOps choisit comment **Loki et Alloy** s'exécutent :

- **Docker Compose** : images gérées, volumes `autokom-logs_loki-data` et `autokom-logs_alloy-data`, ports publiés exclusivement sur `127.0.0.1` ; aucun exécutable `loki` ou `alloy` n'est installé sur l'hôte.
- **Services natifs systemd** : binaires officiels Loki et Alloy installés depuis le dépôt signé Grafana ; port `127.0.0.1:3100` pour Loki, `127.0.0.1:12345` pour Alloy ; logs systemd, découverte Docker des applications existantes. Le moteur **Docker des applications métier ne doit jamais être désinstallé**.

**Un seul couple Loki/Alloy actif est autorisé.** Les dashboards Grafana, métriques Prometheus, règles Alertmanager et sonde E2E sont conservés dans les deux modes. Le choix « moteur de logs » (`loki`, `opensearch`, `both`) est différent du choix « exécution » (`docker`, `systemd`). **La version native automatisée de v23 couvre Loki + Alloy uniquement** : un déploiement OpenSearch ou dual reste Docker. Une migration qui risquerait de supprimer OpenSearch est bloquée.

## Commandes

```bash
# Inventaire et plan (aucune modification)
autokom logs runtime
autokom logs runtime systemd --check
autokom logs runtime docker --check
sudo autokom install logs --check

# Menu TTY : 1 / 2, ↑ / ↓, Entrée, 0 / q pour annuler
sudo autokom install logs

# Installation initiale native, choix explicite non interactif
sudo autokom install logs --backend loki --runtime systemd

# Installation initiale Docker, choix explicite non interactif
sudo autokom install logs --backend loki --runtime docker

# Migration : nécessite une seconde confirmation TTY explicite
sudo autokom logs runtime systemd --apply
sudo autokom logs runtime docker --apply

# Maintenance du mode actif
sudo autokom logs reconcile --check
sudo autokom logs reconcile --apply
sudo autokom logs verify
sudo autokom logs doctor

autokom tools show loki
autokom tools show alloy
```

## Migration et sauvegarde

Une transition active Docker ↔ systemd demande **deux décisions** : le choix du mode, puis une confirmation de migration/retirage. L'opération :

1. Détecte la présence des services actifs et refuse les modes mixtes. Elle refuse les configurations Docker inconnues/modifiées et les installations natives non gérées.
2. Prépare le nouveau mode et archive les configurations dans un répertoire racine `0700` sous `/var/backups/autokom/logs/<UTC_TIMESTAMP>/`.
3. Arrête **seulement Loki et Alloy** dans l'ancien mode ; les applications ETakalo, AgriConnect, Diakom, Jenkins, Nginx, Prometheus, Grafana et Docker Engine ne sont pas arrêtés.
4. Copie le stockage Loki : depuis le conteneur arrêté vers le stockage natif, ou depuis le stockage natif arrêté vers le volume Docker Compose ; le nouveau mode reprend la même configuration logique de rétention.
5. Vérifie `/ready`, `/-/ready` et une **véritable sonde journald → Alloy → Loki** ; si l'un échoue avant la suppression, le mode source est redémarré.
6. Supprime les services/containeurs de l'ancien mode après succès. Les sauvegardes sont **volontairement conservées** pour récupération. L'ancien binaire APT et ses fichiers gérés sont purgés après passage à Docker ; les conteneurs et volumes Loki/Alloy gérés sont supprimés après passage à systemd. Les images Docker ne sont supprimées que si Docker l'autorise (elles peuvent être partagées).

### Conditions de réussite / arrêt sécurisé

- Migration TSDB : versions natives **pinnées** sur `loki=3.7.0`, `alloy=1.11.0` pour correspondre aux images Docker v22. Si APT ne propose plus ces versions, **la migration est refusée avant l'arrêt de Docker** ; le correctif ne met pas à jour silencieusement le format des données.
- Le passage vers systemd est bloqué si un conteneur OpenSearch du projet `autokom-logs` est détecté, y compris arrêté.
- Le mode natif collecte les logs Docker via le socket Docker ; la présence de l'utilisateur `alloy` dans le groupe `docker` équivaut à un privilège élevé. Il est recommandé de le remplacer par un socket proxy restreint avant production multi-tenant.
- L'espace disque doit pouvoir contenir une sauvegarde **et** une copie des données. Une copie incomplète doit interrompre la procédure. Pendant la bascule, il peut y avoir une courte interruption de collecte.
- En cas d'erreur après la bascule mais avant nettoyage, vérifier les statuts et journaux de la machine. Ne pas supprimer manuellement les volumes avant une preuve d'ingestion.
- Les fichiers de sauvegarde sont privés, mais il est recommandé de les exporter sur un stockage hors instance (snapshot EBS chiffré avec test de restauration), particulièrement avant une migration de production.
- Ne jamais lancer `docker compose down -v` sur ce serveur : d'autres applications et moteurs de recherche peuvent partager le démon Docker.

## Contrôles post-installation

```bash
sudo autokom logs doctor
sudo autokom logs verify
curl -fsS http://127.0.0.1:3100/ready
curl -fsS http://127.0.0.1:12345/-/ready
ss -tuln | grep -E ':(3100|12345)\b'
```

Si **systemd** : `systemctl status loki alloy` ; `journalctl -u loki -u alloy --since '10 minutes ago'` ; `loki --version` et `alloy --version`.

Si **Docker** : `docker ps --filter label=com.docker.compose.project=autokom-logs` ; `docker inspect` pour vérifier les ports. La commande `loki` peut être absente sur l'hôte en mode Docker : c'est normal.

## Périmètre de validation

Les tests unitaires et de CLI sont lancés sur une copie de v22. Le paquet **ne peut pas être déclaré prêt pour la production sur EC2** avant validation des versions APT disponibles, de la santé réelle après publication des ports, de la migration de données et des alertes de l'environnement. Aucune modification n'a été effectuée sur l'instance EC2 par la préparation de ce correctif.
