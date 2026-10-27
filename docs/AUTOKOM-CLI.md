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

La grande bannière officielle AUTOKOM (`scripts/autokom-banner.sh`) est
**désactivée par défaut** pour garder une CLI rapide et compacte. Elle s'affiche
seulement avec `-b` ou `--with-banner`, avant **ou après** la sous-commande.
Les cadres compacts et leurs couleurs restent visibles sans cette option.
Les grandes bannières Jenkins, Prometheus, Grafana, Kibana et Wazuh gardent leur
règle indépendante : **première installation effective uniquement**.

```bash
autokom -v                     # version produit et révision Git (sans bannière)
autokom -b -v                  # même résultat, avec bannière
autokom --with-banner status  # inventaire, avec bannière
sudo autokom install -b --check # simulation, avec bannière
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

## Version produit stable, revision Git dynamique

Le fichier `VERSION` a la racine fixe la **version de produit** (`0.1`). Elle
reste identique entre deux mises a jour Git tant que le mainteneur ne modifie
pas le fichier et ne publie pas ce changement. La revision, elle, suit Git.

```text
AUTOKOM · version 0.1
AUTOKOM · révision 6b8d6cd
```

`autokom -v`, `autokom --version` et `<sous-commande> -v/--version`
montrent les deux lignes. Toutes les sous-commandes possedent `-h`/`--help`,
avec un exemple adapte. Ni aide ni version ne demarrent un scan systeme.

## Feedback et progression

- `✓` vert clair = controle reussi, `+` bleu clair = action attendue,
  `!` jaune clair = vigilance, `-` gris = phase ignoree,
  `✗` rouge = erreur, `›` bleu clair = information.
- Seul le signe est colorise : les alignements ne bougent pas. Les
  journaux/redirections, `NO_COLOR` et `TERM=dumb` restent sans ANSI.
- `autokom status` et les inspections silencieuses de `install` affichent un
  **loader temporaire**, uniquement en TTY et uniquement lorsque la verification
  prend assez de temps. Les menus interactifs ne sont pas masques.
- `autokom install` affiche un compteur/barre **de phases traitees** (`1/4`,
  `2/4`, etc.), jamais un pourcentage fictif de telechargement ou de duree.
- Les gros logos Jenkins, Prometheus et Grafana restent reserves a leur
  premiere installation ; la banniere AUTOKOM est optionnelle via `-b` / `--with-banner`.
- Les operations de pare-feu, service, base, certificat et Jenkins ne sont pas
  modifiees par ce correctif de presentation.

## Loader d'activité et écoute Jenkins (correctif v6)

- Les questions `? ... [o/N]` restent **immobiles** tant qu'une réponse est attendue : aucun spinner ne doit se superposer au texte tapé.
- **Après** validation, le contrôle de l'écoute privée Jenkins affiche un loader Braille discret pendant `systemctl restart` et la vérification des sockets. La ligne est effacée avant le résultat final. `autokom update` fait de même pendant `git pull` ; les messages Git sont restitués une fois l'opération terminée.
- Le loader n'apparaît jamais dans les fichiers journaux, les sorties redirigées ou avec `NO_COLOR`. Une tâche terminée s'annonce par `✓` ou `✗` ; aucune progression temporelle fictive.
- Sur Ubuntu/Jenkins, `ss` peut indiquer `127.0.0.1:8080`, `[::1]:8080` ou `[::ffff:127.0.0.1]:8080` pour une écoute privée. Le dernier format est IPv4-mappé dans IPv6, **pas une exposition publique**. AUTOKOM traite correctement les trois.
- `0.0.0.0:8080`, `*:8080` et `[::]:8080` restent des écoutes publiques. AUTOKOM exige l'accord avant redémarrage, ne touche pas aux autres overrides systemd et restaure son fichier de configuration si la nouvelle écoute ne devient pas privée. Le message de diagnostic filtre les variables sensibles.
- Le fichier `/etc/systemd/system/jenkins.service.d/20-autokom-loopback.conf` appartient exclusivement à AUTOKOM. Un autre override pouvant réimposer l'adresse d'écoute doit être inspecté par l'administrateur : ne jamais écraser automatiquement les overrides tiers.

Après `autokom update`, exécuter `sudo autokom install --check` puis `sudo autokom install`. En cas de blocage persistant, vérifier **localement**, avant de partager une sortie expurgée :

```sh
sudo systemctl cat jenkins
sudo ss -lntp | grep ':8080'
sudo journalctl -u jenkins -n 50 --no-pager
```

**Sécurité :** ne pas copier/coller d'identifiants, secrets d'environnements, tokens, mots de passe ou URL signées contenus dans les overrides ou les journaux. L'accès extérieur doit aussi être refusé sur 8080 dans le Security Group AWS, indépendamment du diagnostic local.
