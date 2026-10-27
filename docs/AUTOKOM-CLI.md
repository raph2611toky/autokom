# AUTOKOM : point d'entrée unifié

## Commande unique et parcours

`sudo autokom install` exécute un **inventaire**, affiche son plan, puis parcourt les phases suivantes dans l'ordre logique : fondation → Jenkins/service → plugins Jenkins → Prometheus/Grafana. Chaque phase est à nouveau inspectée juste avant de démarrer ; les éléments conformes sont ignorés. En cas d'erreur la progression s'interrompt : aucune « réussite » n'est annoncée par anticipation.

```
AUTOKOM / INSTALLATION ADAPTATIVE
✓ Fondation système              déjà conforme, ignorer
+ Jenkins / services              écouter sur 127.0.0.1 (confirmation)
+ Plugins et sécurité Jenkins     plugins manquants (token administrateur)
+ Prometheus / Grafana            à installer et relier (domaines facultatifs)
```

Seuls les changements sensibles ou les valeurs inconnues donnent lieu à un menu : premier choix de base SQL, domaine, identifiants ou token administrateur, accès Docker root-équivalent, redémarrage d'un service existant. La post-configuration Jenkins fait appel à son assistant officiel lorsque le premier administrateur n'a pas encore été créé. Le programme ne contourne pas ce mécanisme.

Le gros logo d'une application apparaît uniquement lors de sa **première installation réelle**, lorsque le composant n'existait pas. Les commandes `status`, `check`, `config`, `install --check`, ainsi que les reprises d'installation, utilisent des cadres compacts.

## Sur un serveur EC2 AUTOKOM existant

```bash
sudo git -C /opt/autokom/source pull --ff-only
sudo bash /opt/autokom/source/install.sh --link-cli

autokom status
sudo autokom install --check
sudo autokom install
sudo autokom check
```

Si Jenkins écoute encore sur `*:8080`, l'étape **Jenkins / services** demande avant de poser l'override `JENKINS_LISTEN_ADDRESS=127.0.0.1` et de le redémarrer. En cas d'échec, AUTOKOM essaie de restaurer l'ancien override. Cela reste une intervention sensible : sauvegarde et accès de secours recommandés.

Si Jenkins est prêt mais que Prometheus ou Grafana manque :

```bash
sudo autokom install --from observability
```

Cette variante est conditionnelle : Jenkins doit être actif et son interface locale correctement isolée. Elle ne contourne pas le contrôle de ses prérequis.

## Personnaliser et expertiser

`autokom config` lit `config/observability/defaults.ini` et, s'il existe, `/etc/autokom/observability.ini`. Aucune clé ou token n'est stocké dans ces fichiers. La commande experte reste disponible pour intervenir uniquement sur les fonctionnalités voulues :

```bash
sudo autokom configure plugins
sudo autokom configure observability
sudo bash /opt/autokom/source/scripts/security/network-audit.sh
sudo python3 /opt/autokom/source/scripts/verify/stack.py --strict
```

Les noms de fichiers canoniques ne sont plus numérotés. Les anciens scripts numérotés servent seulement à ne pas casser les automatismes qui ont été créés avec les premières phases.

## Vérifications effectuées / limites

- `install --check` et `status` : lecture seule, aucun service modifié.
- `install` : écoute Jenkins locale, état systemd, plugins présents sur disque, disponibilité Prometheus/Grafana, cible Prometheus UP, datasource Grafana provisionnée, vhosts/certificats des domaines configurés.
- Le plugin Jenkins peut être téléchargé sur disque sans être immédiatement **activé** : AUTOKOM attend le redémarrage du processus avant la phase suivante.
- La vérification Grafana sur disque ne remplace pas un test API authentifié. Le module observabilité effectue une vérification API lorsqu'il dispose des identifiants nécessaires.
- Aucun outil local ne peut prouver seul la fermeture d'un AWS Security Group. Vérifier les ports exposés côté EC2 et les règles de publication Docker.
- Une cible déjà installée mais modifiée manuellement peut nécessiter `autokom configure <module>` ; l'automatisation ne doit pas détruire les configurations qu'elle ne reconnaît pas.

## Signature AUTOKOM et raccourcis

Toutes les commandes `autokom` (dont `status`, `check`, `config`, `install`,
`update`, l'aide et la version) commencent par **la bannière officielle
AUTOKOM** (`scripts/autokom-banner.sh`). Cette bannière appartient au programme
principal ; les grandes bannières Jenkins, Prometheus, Grafana, Kibana et Wazuh
restent réservées à leur **première installation effective**.

```bash
autokom -v                     # révision/tag Git actuelle
autokom --version
autokom -h                     # commandes disponibles
autokom --help
autokom install -h             # options de l'installation
autokom status --version      # disponible aussi après sous-commande
```

La version est calculée avec `git describe --tags --always --dirty` :
une mise à jour de la branche affiche automatiquement la nouvelle révision,
sans numéro codé en dur. Sans Git, un message lisible signale que la révision
est indisponible ; `-h` et `-v` n'essaient jamais de contacter Jenkins ou les
services de l'infrastructure.

Les **en-têtes compacts** reprennent la palette des services :
cyan clair pour AUTOKOM/Jenkins, vert clair pour la fondation, jaune chaud
pour Prometheus et l'observabilité, orange clair pour Grafana, jaune clair
pour la sécurité. Les couleurs ANSI apparaissent uniquement sur terminal
TTY compatible ; elles sont supprimées dans les logs, les redirections,
ou si `NO_COLOR` est défini.

Le correctif est strictement visuel/ergonomique : aucun port, domaine,
pare-feu, service systemd, plugin ou identifiant n'est modifié.
