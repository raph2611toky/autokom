# AUTOKOM v24 — Audit Grafana/Loki et activité CLI

## Portée

Cette révision s'appuie sur **v23**. Elle ne réinstalle pas les services Loki,
Alloy, Prometheus, Jenkins ou Grafana. Elle conserve les UIDs Grafana et les
modes `docker` et `systemd`, sans changement de données TSDB ni de rétention.

Le canari `PASS` démontre une ingestion **journald** pour son événement.
Il ne prouve ni l'ingestion Docker/Nginx ni la validité de toutes les requêtes
Grafana. De même, les trois targets Prometheus `UP` valident la collecte de
métriques, pas la configuration des datasources côté Grafana.

## Application depuis une v23 déjà opérationnelle

```bash
cd /opt/autokom/source
git status --short
# Après vérification du patch fourni et merge dans la branche gérée :
autokom logs audit
sudo autokom logs reconcile --check
sudo autokom logs reconcile --apply
sudo autokom logs verify
autokom logs audit
```

`reconcile --apply` met à jour uniquement les fichiers AUTOKOM reconnus par
leur manifeste SHA. Une modification manuelle non reconnue entraîne un refus
plutôt qu'un écrasement silencieux. Les dashboards sont scannés par Grafana
suivant sa configuration de provisioning ; aucun redémarrage systématique n'est
imposé. Pour un problème de datasource non chargée, vérifier `/etc/grafana/`
`provisioning/datasources/autokom-loki.yaml` puis envisager un redémarrage
**contrôlé** de `grafana-server` si un rechargement en place est indisponible.

## Audit : explications

`autokom logs audit` (lecture seule, sans sudo nécessaire lorsque les fichiers
sont lisibles) vérifie :

- API Loki, séries/labels réels sur 24 heures et présence des sources observées ;
- trois jobs Prometheus ;
- requêtes LogQL des panneaux effectivement provisionnés (sans imprimer leur contenu) ;
- UIDs Loki et Prometheus via l'API Grafana si l'accès est autorisé ;
- correspondance nombre de panneaux chargés/fichiers si un jeton API est fourni.

Une réponse `HTTP 400` sur un panneau est une **erreur LogQL** ; un résultat
`OK (no matching lines/series ...)` signifie que la requête est acceptée mais
qu'elle ne rencontre aucune ligne sur la période. Une icône rouge dans Grafana
nécessite de consulter **Panel → Inspect → Query** pour connaître l'erreur réelle.

L'API Grafana peut exiger une authentification. Utiliser un jeton de compte de
service avec les droits de lecture des datasources et dashboards ; l'exporter
temporairement dans la session, sans l'ajouter à un fichier, à l'historique ou
à une commande affichant ses valeurs :

```bash
read -rsp 'Jeton Grafana (lecture seule) : ' AUTOKOM_GRAFANA_TOKEN; echo
export AUTOKOM_GRAFANA_TOKEN
autokom logs audit
unset AUTOKOM_GRAFANA_TOKEN
```

Le script n'affiche et ne persiste jamais le jeton. Par défaut, il tente les
endpoints locaux `127.0.0.1:3000`, `:3100` et `:9090`. Le jeton n'est transmis
qu'au serveur Grafana local.

## Vérifier l'origine des données manquantes

Ouvrir **Grafana → Explore → AUTOKOM Loki**, période 24 heures. Tester :

```logql
{platform="autokom"}
{platform="autokom",source="systemd"}
{platform="autokom",source="docker"}
{platform="autokom",source="nginx"}
```

Le dashboard principal place désormais les *flux bruts* en tête et n'en dépend
pas des filtres. Les variables `Source`/`Service` sont émises sous le modèle
structuré Loki « Label values » au lieu de la forme texte héritée.

Un graphique de sécurité peut légitimement indiquer `No data` lorsqu'aucun
événement ne correspond. N'ajoutez pas de faux incidents pour le remplir.
L'absence de source `nginx` ou `docker` doit au contraire être étudiée avec
les permissions d'Alloy, les chemins et les paramètres de collecte.

## Activité CLI

Les appels silencieux de sous-processus dans les phases logs et migration
utilisent désormais le même `Activity` que la CLI existante. Le spinner est
visible après un court délai sur un vrai TTY couleur, puis effacé avant
l'affichage du résultat. Les menus et les commandes `--check` conservent leurs
comportements, sans estimation de progression inventée et sans traces ANSI
dans les logs non interactifs.

## Plan de retour arrière

Conserver une copie des fichiers AUTOKOM et le commit précédent ; si les
nouveaux dashboards provoquent une régression, restaurer les fichiers du
commit antérieur, puis relancer `sudo autokom logs reconcile --apply` uniquement
avec un manifeste compatible (sinon restaurer fichiers et manifeste ensemble
à partir de la sauvegarde). Ne pas supprimer les volumes Loki, ni faire
`docker compose down -v`.
