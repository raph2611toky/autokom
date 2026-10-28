# AUTOKOM — DevSecOps autonome

AUTOKOM déploie puis maintient un environnement DevSecOps sur Ubuntu avec une **commande principale**. Il détecte les composants présents, ne relance pas les opérations conformes et s'arrête lorsqu'une intervention risquée nécessite une confirmation.

## Démarrage sur AWS

Première activation de la commande globale, **sans installation de services** :

```bash
sudo bash /opt/autokom/source/install.sh --link-cli
```

Puis, depuis n'importe quel dossier :

```bash
autokom status                 # Inventaire compact
sudo autokom install --check  # Plan sans modification ni question
sudo autokom install          # Appliquer les actions nécessaires
sudo autokom check            # Audit complet après installation
```

Pour une nouvelle machine Ubuntu, le bootstrap public installe le dépôt et le lanceur (commande explicite) :

```bash
curl -fsSL https://raw.githubusercontent.com/raph2611toky/autokom/main/install.sh | sudo bash -s -- --link-cli
sudo autokom install
```

## Commandes courtes

| Commande | Effet |
|---|---|
| `autokom status` | Résumé rapide, lecture seule |
| `sudo autokom install --check` | Affiche les étapes à traiter sans changement |
| `sudo autokom install` | Installation et réparation sélective des composants |
| `sudo autokom install --from observability` | Reprend à l'observabilité, sous réserve des prérequis |
| `sudo autokom install --only plugins` | Traite une phase particulière |
| `sudo autokom check` | Audit local détaillé (ne teste pas les Security Groups AWS) |
| `autokom config` | Affiche les ports/URLs configurés, sans secret |
| `sudo autokom configure services` | Reconfiguration experte interactive Jenkins / SQL |
| `sudo autokom configure plugins` | Reconfiguration des plugins Jenkins |
| `sudo autokom configure observability` | Reconfiguration Prometheus / Grafana |
| `sudo autokom update` | Mise à jour Git explicite si le dépôt est propre |

Les scripts spécialisés restent disponibles dans `scripts/install/` et `scripts/verify/` sous des **noms explicites** : `foundation.sh`, `services.sh`, `jenkins.sh`, `observability.sh` et `stack.py`. Les anciens chemins numérotés sont de simples alias temporaires de compatibilité et ne font plus partie du parcours normal.

## Sécurité et périmètre

- Aucun secret dans le dépôt ni dans la configuration dynamique `config/observability/defaults.ini`.
- Aucune création ou modification silencieuse des bases MySQL/PostgreSQL sur une instance Jenkins existante.
- Jenkins, Prometheus et Grafana doivent écouter localement ; l'accès par domaine se fait via Nginx/HTTPS.
- Les modifications nécessitant un redémarrage de Jenkins déjà utilisé demandent une confirmation explicite.
- L'activation d'UFW et les Security Groups cloud ne sont **pas** modifiés silencieusement. Les règles Docker publiées exigent un audit spécifique.
- Une vérification via fichier de provisioning Grafana ne prouve pas à elle seule l'authentification et l'accès réel à la source : utiliser aussi l'API Grafana pendant la configuration et la validation fonctionnelle.

Voir [documentation détaillée](docs/AUTOKOM-CLI.md).

## Observabilité v17

Les dashboards Serveur/Jenkins/Docker/Collecte sont provisionnés pendant `autokom configure observability`. cAdvisor est proposé pour les métriques Docker. La fusion des règles vides, leur rechargement et leur contrôle via API sont corrigés. Voir [diagnostic, mise à jour et test SMTP de bout en bout](docs/OBSERVABILITY-v17.md).

### Supervision avancée des journaux (v21)

Voir [`docs/LOGGING-OBSERVABILITY-v21.md`](docs/LOGGING-OBSERVABILITY-v21.md) pour
l'intégration Alloy/Loki dans Prometheus, Grafana, Alertmanager et la sonde
end-to-end systemd. Mise à jour non destructive d'une installation existante :
`sudo autokom logs reconcile --check` puis `sudo autokom logs reconcile --apply`.

### Journalisation v22 — diagnostic réseau et endpoints réels

Le correctif v22 traite les ports Docker non publiés sous certains Docker Engine
avec réseau `internal: true`. Le réseau isolé de collecte reste présent,
mais un bridge additionnel permet aux seules API locales de Loki, Alloy et
OpenSearch de publier leurs ports sur `127.0.0.1`. Les commandes
`autokom tools show loki`, `autokom tools show alloy` et
`autokom logs doctor` vérifient endpoints, ports réellement publiés,
métriques et erreurs. Procédure : [`docs/LOGGING-NETWORK-v22.md`](docs/LOGGING-NETWORK-v22.md).

### Choix Docker Compose / services systemd pour Loki + Alloy (v23)

Le choix d'exécution appartient à l'administrateur. `sudo autokom install logs`
affiche le menu commun AUTOKOM (navigation ↑/↓, raccourcis numériques, annulation).
Pour consulter le mode actif : `autokom logs runtime`. Pour une migration planifiée :
`sudo autokom logs runtime systemd --check`, puis
`sudo autokom logs runtime systemd --apply` avec confirmation TTY, sauvegarde,
validation d'ingestion et retrait contrôlé du mode Docker précédent.
Voir [guide de migration v23](docs/LOGGING-RUNTIME-v23.md).

## v25 — Audit du chemin Grafana → Loki et plugins dynamiques

- `autokom logs grafana --check` : configuration Grafana chargée (SQLite si disponible) et requête Grafana API (si jeton volontaire).
- `sudo autokom logs grafana --apply` : recharger **uniquement** Grafana lorsqu'un décalage de provisioning est prouvé, après confirmation.
- `autokom grafana plugins search "logs drilldown" --type app --select` : catalogue en direct avec recherche et filtres.
- `sudo autokom grafana plugins install PLUGIN_ID --check|--apply` : fiche officielle et installation contrôlée.
- Détails : `docs/GRAFANA-LOGS-PLUGINS-v25.md`.
