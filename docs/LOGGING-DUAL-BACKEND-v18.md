# AUTOKOM — Journaux dual-backend (Loki/Alloy et OpenSearch)

Version de travail : extension de la base v17 (archive `autokom.zip`, révision `283814f` récupérée dans la bibliothèque). **La validation réelle sur l'EC2 n'est pas effectuée ici.** Le code Loki/Alloy éventuellement déjà installé depuis la session Work postérieure doit être fusionné avant application ; ne pas écraser cette configuration sans revue.

## Architecture

```text
Docker (dont Jenkins s'il est conteneurisé) ────┐
Journald système (dont Jenkins systemd) ────────┼→ Grafana Alloy ─────┬→ Grafana Loki (7j) → Grafana / LogQL
Nginx /var/log/nginx/*.log ──────────────────────┘                     └→ OTLP/gRPC → Data Prepper → OpenSearch (ISM 14j) → Dashboards / recherche
```

En mode `both`, **les deux moteurs collectent en parallèle**. `autokom logs use loki|opensearch` définit la **préférence de consultation AUTOKOM** dans `/etc/autokom/state/logs-backend.json` ; cela ne désinstalle rien, ne stoppe aucun flux et ne transfère aucun historique. L'interface métier AUTOKOM devra consommer cette préférence dans une phase ultérieure. La CLI ne prétend pas encore piloter une interface web non développée.

Les collecteurs sont isolés via un réseau Compose dédié et exposent les APIs seulement sur `127.0.0.1`. Grafana existant reste sur son installation actuelle : ajout d'une datasource Loki et du dashboard `AUTOKOM · Journaux`, sans toucher aux dashboards/alertes Prometheus.

**À propos du nom :** il s'agit d'**OpenSearch + OpenSearch Dashboards + Data Prepper**, et non de la distribution Elasticsearch/Logstash/Kibana. On peut ensuite ajouter des parsers et tableaux par application.

## CLI

```bash
autokom install -h
autokom install --only logs --backend both --check
sudo autokom install --only logs --backend both
sudo autokom install --only observability
sudo autokom install --only pipeline
sudo autokom configure logs
autokom logs status
sudo autokom logs use opensearch
sudo autokom logs use loki
```

Alias disponibles dans l'aide : `core` = foundation, `pipeline`/`cicd` = services et plugins, `metrics`/`monitoring` = observability, `logging` = logs. L'installation habituelle `sudo autokom install` conserve les étapes historiques et **n'ajoute pas OpenSearch automatiquement** pour éviter une consommation mémoire imprévue.

## Prérequis avant application

1. Ubuntu avec Docker Engine et `docker compose` v2 opérationnels. Arrêter toute autre installation Loki utilisant `127.0.0.1:3100` ; ne jamais écraser des volumes/configurations existants. Vérifier `sudo ss -lntp | grep -E ':(3100|9200|5601) '`.
2. Les deux piles sur le même hôte sont exigeantes : **12 GiB de RAM conseillés** ; refus en dessous de **8 GiB**, et au moins **20 GiB libres** recommandés (plus selon volume de logs). Les images Docker nécessitent un accès au registre ; prévoir volumes EBS et sauvegardes.
3. Pour OpenSearch : `vm.max_map_count` >= `262144`. Configurer explicitement si besoin : `echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-autokom-opensearch.conf && sudo sysctl --system`.
4. **Pas de publication publique** des ports 3100/9200/5601 ; ne pas les ouvrir dans les Security Groups AWS. Accès ponctuel via tunnel SSH ou Nginx avec TLS + authentification forte.
5. Avant de remplacer une configuration postérieure à l'archive v17, réaliser une revue/diff. L'installateur refuse de réécrire un fichier modifié à la main dans `/etc/autokom/logs`.

## Sécurité et maturité

- OpenSearch active son plugin de sécurité avec mot de passe administrateur fort généré lors de la première installation (`/etc/autokom/logs/.env`, mode 0600). **Son profil Docker initial utilise encore la PKI de démonstration OpenSearch.** Data Prepper utilise `insecure: true` pour parler à cette PKI **uniquement sur un réseau Docker interne**. Ceci constitue un bootstrap contrôlé, **pas une configuration de production complètement durcie**. Avant exposition réseau, remplacer CA/certificats, créer un compte d'ingestion avec droits minimaux, corriger les secrets et valider la chaîne TLS.
- Alloy accède au socket Docker en lecture seule au niveau du montage, mais **l'API du socket reste très privilégiée même en `:ro`** : le conteneur est root dans cette version de bootstrap. Prévoir un Docker socket proxy restreint, utilisateur non root, puis supprimer l'accès direct au socket pour une exploitation durcie.
- L'ingestion OTLP/gRPC de Data Prepper est sans TLS **dans le réseau Compose interne uniquement**. Ne pas publier ce port. Les mots de passe ne doivent pas être copiés dans les tickets ou logs.
- Loki est sans authentification interne (`auth_enabled: false`) : il **doit rester privé** derrière une authentification proxy si un accès externe est requis.
- Les secrets applicatifs peuvent apparaître dans les journaux : implémenter politiques de redaction / exclusion avant extension à des applications sensibles.
- Les logs des composants de stockage et du collecteur sont exclus de la découverte Docker pour éviter une ingestion récursive.
- Rétention : Loki 168h / 7 jours via compactor ; OpenSearch ISM 14 jours pour les nouveaux index `autokom-logs-*` ; à ajuster après capacité EBS et obligations légales.

## Vérification de fonctionnement (à faire sur EC2)

```bash
sudo autokom install --only logs --backend both --check
sudo autokom install --only logs --backend both
sudo docker compose -f /etc/autokom/logs/compose.yaml --env-file /etc/autokom/logs/.env --profile loki --profile opensearch ps
curl -fsS http://127.0.0.1:3100/ready
curl -fsS http://127.0.0.1:3100/loki/api/v1/labels
sudo docker compose -f /etc/autokom/logs/compose.yaml --env-file /etc/autokom/logs/.env --profile loki --profile opensearch logs --tail 40 alloy data-prepper
```

Pour OpenSearch, **ne pas afficher le mot de passe dans le shell ou les logs**. Vérifier depuis le navigateur, avec tunnel SSH, `http://127.0.0.1:5601` et se connecter avec le secret local. Créer le motif d'index `autokom-logs-*` s'il n'existe pas automatiquement. Vérifier l'ISM dans Index Management.

Émettre un message `AUTOKOM_LOG_E2E_<identifiant>` depuis un service de test connu (journal systemd ou stdout d'un conteneur étiqueté) ; rechercher le même identifiant dans Grafana Explore **et** dans OpenSearch Dashboards. Cette vérification prouve le trajet complet source → Alloy → deux backends. Vérifier ensuite la latence d'ingestion, mémoire JVM, volume indexé, règles d'alerte, permissions d'accès et sauvegarde/restauration.

## Conduite de changement

Ce correctif n'est pas poussé sur GitHub et n'est pas déployé sur l'EC2 : les fichiers sont préparés et testés localement (tests Python + parsing YAML). Aucun test Docker réel n'était possible dans cet environnement. La compatibilité exacte des images et des flux doit être vérifiée sur l'hôte avant mise en production. La transition vers un environnement pleinement durci fera partie de l'étape d'intégration live.

## Mise à jour v19 : RAM et inventaire

Sur un hôte disposant de moins de **12 Gio** de RAM physique, une demande
`sudo autokom install logs` (ou `--only logs --backend both`) propose
explicitement un backend unique. Aucun choix n'est appliqué silencieusement.
Le choix est demandé sur le terminal interactif avant tout lancement de
Docker Compose. Une installation non interactive doit indiquer `--backend
loki` ou `--backend opensearch`. Si la RAM est inférieure à 5 Gio, OpenSearch
n'est pas proposé ; le garde-fou propre à chaque backend reste actif.

Le mode `--check` donne les avertissements et les options sans demander de
réponse ni modifier la configuration. Sur un serveur de 7,6 Gio déjà chargé
par Jenkins/Grafana, **Loki + Alloy est le choix privilégié**. OpenSearch
exige `vm.max_map_count >= 262144` et peut nécessiter davantage de mémoire
au démarrage. Une installation `both` peut être refaite après augmentation de
la RAM, en tenant compte de la mémoire consommée par les services existants.

Inventaire des composants Docker, sans unités systemd artificielles :

```bash
autokom tools list --all
autokom services --all         # alias de list --all
autokom tools show --all       # alias de list --all
autokom tools show loki
autokom tools show opensearch
autokom tools alloy files
```

En mode OpenSearch seul, AUTOKOM ne crée pas de tableau de bord Grafana qui
ciblerait un Loki absent. L'état remonté dans l'inventaire reflète le
conteneur Docker (`active`, `inactive`, `absent`), pas une preuve d'ingestion.
Il faut toujours valider l'arrivée de vrais journaux et les requêtes après
installation.

## Mise à jour v20 : menus AUTOKOM unifiés (sans régression)

L'installation adaptative des logs utilise **exactement le sélecteur commun**
`scripts/lib/menu.py:pick` déjà employé par la configuration des bases
PostgreSQL/MySQL et par Jenkins. Elle ne lit plus les flèches comme une
chaîne de caractères dans une saisie canonique.

En terminal interactif avec moins de 12 Gio, sélectionner par **↑/↓ puis
Entrée**, par **1**, **2** (ou **3** si la RAM permet la double stack), ou
appuyer directement sur **Entrée** pour le premier choix recommandé. La
ligne **0. Annuler sans modifier l’infrastructure** est accessible par les
flèches, la touche **0**, **q** ou **Ctrl-C**. Le menu conserve le titre
cyan, le curseur vert, les messages d'avertissement et le nettoyage visuel
avant l'installation ; aucune animation n'est démarrée pendant la question.

Le composant commun possède maintenant un paramètre facultatif
`cancel_label`, sans modifier la signature ou le comportement des menus
anciens quand il n'est pas fourni. Les commandes `--check`, `--backend
loki`, `--backend opensearch` et les alias d'installation restent identiques.
Pas de basculement implicite vers un moteur lorsque le terminal est absent.

Les tests couvrent les chemins numériques, les touches ↑/↓, Entrée, 0,
q et les annulations, y compris sur un pseudo-terminal effectif, en plus
de la suite de non-régression historique.
