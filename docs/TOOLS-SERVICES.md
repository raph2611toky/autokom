# AUTOKOM · Outils & services (v0.1)

## But

`autokom tools` est un **catalogue opérationnel**, pas un nouvel orchestrateur qui réinstalle tous les composants. `autokom services` est un alias strict. Toutes les sondes `list`, `show` et `show -e` sont en lecture seule. Les outils absents sont visibles avec `list --all`.

```bash
autokom tools list
autokom tools list --all
autokom tools show jenkins
autokom tools show jenkins -e
sudo autokom tools show jenkins -E
sudo autokom tools configure jenkins
sudo autokom tools restart jenkins
sudo autokom tools stop grafana
sudo autokom tools change prometheus port=9091        # aperçu seulement
sudo autokom tools change prometheus port=9091 --apply
```

### `show` : emplacement, état et documentation

Le catalogue référence les **principaux** fichiers connus (absents / présents), leur chemin absolu, leur fonction et le guide correspondant. Il vérifie aussi les unités systemd, `FragmentPath`, `DropInPaths`, `EnvironmentFiles` et répertoire de travail lorsqu'ils existent. Des plugins tiers peuvent ajouter d'autres fichiers : le catalogue ne prétend pas être une analyse exhaustive de tout le système de fichiers. `systemctl cat <unité>` donne la configuration système effective ; éviter de partager publiquement les fichiers contenant des secrets.

### `-e` et `-E` : environnement

* `-e` affiche exclusivement les **noms** des variables visibles par systemd et, si accessible, dans `/proc/<MainPID>/environ` ; aucune valeur n'apparaît.
* `-E` affiche les noms **et valeurs intégrales** disponibles, dont éventuellement les tokens et mots de passe. Pour éviter toute fuite par pipeline ou collecte de logs, exige root, stdout/stdin en terminal interactif et la confirmation littérale `AFFICHER-SECRETS`. Ne jamais coller sa sortie dans une issue, un chat ou un terminal enregistré. Le programme ne stocke pas la sortie.
* La commande ne peut pas garantir que tous les fichiers `EnvironmentFile` non chargés ou variables créées **après** le démarrage sont lisibles dans `/proc` : `systemctl cat` et le chemin `EnvironmentFiles` affichés par `show` indiquent où vérifier.

### `restart` et `stop`

Ces actions sont limitées à une unité systemd d'un outil connu et nécessitent sudo et une confirmation manuelle par son nom. Elles ne provoquent pas d'écrasement de configuration. Elles peuvent interrompre les applications : avant un redémarrage Jenkins, attendre la fin des jobs. L'arrêt/restart d'UFW via cette interface est refusé pour éviter de perturber les règles SSH. Un service Docker sans unité systemd propre n'est pas géré par ces commandes.

### `configure` et `change`

* `configure jenkins` utilise l'assistant de plugins Jenkins, avec ses confirmations et l'authentification existante.
* `configure prometheus` et `configure grafana` ouvrent l'assistant **commun** d'observabilité (Jenkins → Prometheus → Grafana). Ils ne modifient pas les données existantes sans les contrôles de l'installateur.
* MySQL/PostgreSQL sont inspectables et gérables par systemd, mais **aucune configuration automatique des bases existantes** n'est déclenchée par `tools configure` (pour ne pas toucher aux autres services).
* `change` valide et met à jour uniquement les clés **non sensibles** reconnues dans `/etc/autokom/observability.ini` ; simulation sans `--apply`. Une sauvegarde `.ini.bak` est conservée lors du premier changement. Aucun service n'est redémarré automatiquement : `sudo autokom install --check`, puis `sudo autokom install` pour réconcilier. Les migrations de données historiques nécessitent une procédure séparée. Le port Jenkins réel n'est pas modifiable par ce fichier seul : utiliser l'assistant de services et systemd.


## Mise en forme ciblée : tableaux uniquement pour `show`

Les affichages déjà colorés de `autokom status`, `autokom config`,
`autokom install --check`, `autokom tools list` et `autokom tools change`
**restent inchangés** (lignes compactes, statuts en couleur, pas de tableau).

Les tableaux à colonnes adaptatives ne concernent que :

```bash
autokom tools show jenkins
autokom tools show jenkins -e
sudo autokom tools show jenkins -E
# Alias identique :
autokom services show jenkins
```

- `show` : propriété/valeur, fichiers et leurs rôles, chemins système, documentation.
- `show -e` : variable et « masquée » : aucune valeur exposée.
- `show -E` : variable et valeur après vérification root, TTY et confirmation.
- Les chemins longs sont renvoyés à la ligne plutôt que coupés ; pas de code ANSI dans les journaux.

Le correctif est prévu **directement sur la version v9**, sans installation préalable de la version v10 tabulaire globale.

## Mise à jour Git colorée

Sur un vrai terminal compatible ANSI, les blocs `++++` sont rendus en vert et les blocs `----` en rouge, ainsi que les compteurs finaux. Git assure la récupération ; AUTOKOM ne modifie que la présentation. Les sorties redirigées et `NO_COLOR` restent du texte simple, pour être lisibles par d'autres outils.

## Problème APT rencontré sur AWS

Lors du premier passage, `apt-get install apache2-utils` expirait après 240 secondes. Dans AUTOKOM, ce paquet n'est plus requis pour protéger Prometheus : OpenSSL calcule localement un mot de passe **SHA-512 crypt** haché (secret fourni via stdin), accepté par Nginx `auth_basic_user_file`. La sortie n'affiche pas le mot de passe. Les autres opérations APT (installation Prometheus et Grafana) requièrent toujours des dépôts opérationnels. L'environnement APT est non interactif, l'attente de verrou est bornée et les timeouts produisent un diagnostic plutôt qu'un succès trompeur.

Pour diagnostiquer une installation APT bloquée sur EC2 **sans tuer de processus dpkg ni effacer les verrous** :

```bash
sudo ps -eo pid,etimes,args | grep -E '[a]pt-get|[d]pkg|[u]nattended-upgrades'
sudo fuser -v /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock 2>/dev/null || true
sudo apt-get -o DPkg::Lock::Timeout=30 update
```

Vérifier les sources APT, les résolutions DNS et les journaux APT lorsque l'accès Internet fonctionne mais qu'un miroir ne répond pas. La simple réussite d'un `ping` ne garantit pas le fonctionnement d'APT.

## Après mise à jour sur AWS

```bash
sudo autokom update
autokom tools list
autokom tools show jenkins
autokom tools show jenkins -e
sudo autokom install --check
# Après validation des domaines/ports et de la sécurité :
sudo autokom install
sudo autokom check
```

Les fonctionnalités ont été testées localement avec unités simulées. Leur exécution réelle n'a pas encore été validée sur ce serveur AWS.
