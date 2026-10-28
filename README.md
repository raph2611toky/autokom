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
