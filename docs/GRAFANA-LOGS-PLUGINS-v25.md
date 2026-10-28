# AUTOKOM v25 — Grafana, journaux et plugins vérifiés

## 1. Identifier la panne sans détruire les données

Loki peut répondre correctement (canari PASS, requêtes LogQL OK, cibles Prometheus UP) tandis que **Grafana** ne parvient pas à utiliser la datasource. Ces vérifications concernent deux chemins différents :

- `journald → Alloy → Loki` (ingestion) ;
- `Grafana navigateur → Grafana backend → datasource Loki` (consultation).

AUTOKOM **ne déclare plus** la consultation conforme parce que les métriques Prometheus sont UP.

```bash
autokom logs audit                 # LogQL + configuration Grafana effective si SQLite
sudo autokom logs grafana --check # Inspecter base locale et éventuelle API, sans redémarrage
```

`autokom logs grafana --check` lit uniquement les métadonnées non sensibles de la base SQLite de Grafana lorsqu'elle est présente, notamment le UID `autokom-loki`, le type, l'URL et les requêtes des dashboards chargés. Si Grafana utilise MySQL/PostgreSQL, cette vérification locale est marquée **non disponible**, pas considérée comme un échec de datasource.

Pour effectuer une requête **via Grafana**, et pas directement via Loki, utiliser un **jeton de compte de service Grafana à permissions minimales**, fourni volontairement comme `AUTOKOM_GRAFANA_TOKEN` ; ne jamais le coller dans une discussion, un ticket ou un fichier journal. AUTOKOM ne l'affiche jamais. Le résultat est un statut et un nombre d'échantillons, pas le contenu des logs.

En présence d'un **décalage de provisioning confirmé par SQLite**, une réparation explicite peut redémarrer uniquement `grafana-server` :

```bash
sudo autokom logs grafana --apply
```

Cette commande redemande confirmation en TTY. Elle n'écrase pas les tableaux modifiés : la réconciliation AUTOKOM protège les modifications manuelles et un redémarrage est évité si aucun décalage n'est prouvé. Un échec d'API ou de droits seul ne justifie pas un redémarrage.

Une fois les vérifications passées, rouvrir le dashboard et faire `Ctrl+R`. Dans chaque panneau rouge : **Inspect → Query / Error**, puis vérifier l'UID, l'URL, le statut HTTP et la période. Le dashboard `Flux bruts AUTOKOM` ne dépend pas des variables Source/Service.

## 2. Plugins Grafana — recherche officielle, filtres et installation

L'étape Jenkins `autokom install plugins` demeure réservée aux plugins Jenkins.

```bash
autokom grafana plugins -h
autokom grafana plugins search "logs drilldown" --type app --limit 12
autokom grafana plugins search "graphique" --type panel --select
autokom grafana plugins search "grafana to image"
autokom grafana plugins list
autokom grafana plugins info grafana-lokiexplore-app
sudo autokom grafana plugins install grafana-lokiexplore-app --check
sudo autokom grafana plugins install grafana-lokiexplore-app --apply
```

Alias d'intégration : `autokom tools grafana plugins ...`. Les résultats sont récupérés à la demande depuis le catalogue **https://grafana.com/api/plugins**, classés avec mots-clés et synonymes français/anglais, puis filtrés par type et signature renseignée. AUTOKOM ne fournit pas de résultats inventés et refuse les identifiants non vérifiables, les signatures explicitement invalides et les plugins signalés retirés. Sans réseau, la recherche échoue explicitement. La compatibilité de la version du plugin avec la version de Grafana doit être vérifiée avant d'accepter le changement ; Grafana contrôle aussi la signature au chargement.

`--select` réutilise le menu AUTOKOM (flèches, Entrée, chiffres, annulation) et ne modifie rien. L'installation exige sudo + `--apply` + confirmation interactive, puis redémarre uniquement Grafana. Elle peut interrompre temporairement l'accès aux dashboards et aux alertes Grafana.

### Important : rendu d'images

Depuis Grafana **13**, l'ancien plugin **Grafana Image Renderer** n'est plus pris en charge. Il ne doit pas être installé comme une extension. L'option officielle est un **service renderer séparé** : https://grafana.com/docs/grafana/latest/setup-grafana/image-rendering/ . La documentation recommande au moins **16 Gio de RAM** et **4 CPU**, incompatible avec une installation automatique raisonnable sur l'EC2 AUTOKOM de 7,6 Gio. La recherche de type image précise cette limite et ne déclenche aucune installation.

## 3. Aide, statut et style

Tous les points d'entrée ajoutés acceptent `-h/--help`, même les niveaux hérités (`autokom logs runtime -h`, `autokom tools grafana files -h`, `autokom grafana plugins search -h`). Les statuts de la journalisation, les tableaux de l'inventaire et le plugin manager reçoivent des couleurs ANSI en TTY ; **NO_COLOR**, une redirection ou un terminal non interactif donnent une sortie simple. Les spinners d'attente restent les composants existants ; aucun pourcentage fictif.

## 4. Déploiement et non-régression

Avant de fusionner :

```bash
cd /opt/autokom/source
git status --short
git apply --check /tmp/AUTOKOM-v25-Grafana-Plugins-Validation.patch
git apply /tmp/AUTOKOM-v25-Grafana-Plugins-Validation.patch
```

Aucune migration de Loki ni suppression de Docker n'est demandée : **le mode systemd Loki/Alloy reste préservé**. Un patch de code ne redémarre pas les services. Les tests exécutés localement avec doubles de test ne remplacent pas la vérification réelle de l'EC2.

## Sources officielles

- [Datasource Loki et provisioning](https://grafana.com/docs/grafana/latest/datasources/loki/configure/)
- [Variables Loki](https://grafana.com/docs/grafana/latest/datasources/loki/template-variables/)
- [CLI de gestion des plugins](https://grafana.com/docs/grafana/latest/administration/cli/)
- [Gestion des plugins Grafana](https://grafana.com/docs/grafana/latest/administration/plugin-management/plugin-install/)
- [Image Rendering moderne](https://grafana.com/docs/grafana/latest/setup-grafana/image-rendering/)
