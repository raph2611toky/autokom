> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Cycle de vie des bannières et validation AWS

## Politique d'affichage

- **Installation initiale** (`--apply` et paquet réellement absent) : le grand logo d'origine du service s'affiche une seule fois, uniquement sur terminal interactif. Il n'est pas consigné dans les journaux.
- **Simulation `--check`, vérification, tests, post-installation, service déjà présent, reconfiguration et dépannage** : seulement les petites sections AUTOKOM encadrées, plus les statuts compacts. Les grands logos ne sont jamais déclenchés.
- `AUTOKOM_BANNERS=off` désactive les grands logos même en première installation. `NO_COLOR=1` retire la couleur. Un aperçu manuel reste possible avec `python3 scripts/lib/tool_banner.py grafana --preview`.
- Les logos **Kibana et Wazuh** sont conservés pour les prochaines phases, sans installation ni affichage anticipés.

## Conditions d'interaction

Les questions sont uniquement celles nécessaires à une décision : nouveau domaine, méthode de certificat, identité ou token, nouvelle base/utilisateur, configuration réseau, accès Docker root-équivalent, ou interruption/redémarrage d'un service utilisé. L'état du système et les domaines/HTTPS déjà valides sont réutilisés sans question inutile. Les identifiants sensibles sont saisis via le terminal en mode masqué ; jamais dans une option CLI ou les journaux.

## Ordre AWS recommandé

```bash
# Toujours exécuter avec python3 et un chemin absolu si le cwd n'est pas le dépôt.
sudo git -C /opt/autokom/source status --short
sudo git -C /opt/autokom/source pull --ff-only
sudo python3 /opt/autokom/source/scripts/verify/stack.py --scope all

# Vérifier la configuration et la liaison avant modification
sudo python3 /opt/autokom/source/scripts/lib/observability.py --show-config
sudo bash /opt/autokom/source/scripts/install/services.sh --check
sudo bash /opt/autokom/source/scripts/install/jenkins.sh --check --interactive
sudo bash /opt/autokom/source/scripts/install/observability.sh --check --interactive
```

Le serveur AWS observé possède déjà Jenkins et MySQL. Jenkins écoute encore sur `*:8080` et UFW était inactif à la dernière vérification : **n'appliquez pas la phase 3 avant d'avoir traité cette exposition** (et contrôlé les Security Groups AWS). L'application de la phase 2 peut demander le redémarrage de Jenkins ; prévoir les sauvegardes, les agents/jobs actifs et un accès SSH de secours. L'audit ne modifie jamais le pare-feu ou les services.

Après validation et sauvegardes, appliquer séparément les modules nécessaires : `05-services.sh --apply`, `07-jenkins-postinstall.sh --apply --interactive` puis `08-observability.sh --apply --interactive`. Ne pas réexécuter Jenkins First-Run sur un serveur déjà configuré si seuls Prometheus et Grafana manquent.

Terminer par :

```bash
sudo nginx -t
sudo systemctl is-active jenkins prometheus grafana-server
sudo python3 /opt/autokom/source/scripts/verify/stack.py --scope all --strict
```

La réussite exige **toutes** les liaisons fonctionnelles : exporter Jenkins `/prometheus/` accessible localement (ou avec un compte dédié), cible `jenkins_job` **UP** dans l'API Prometheus et datasource Grafana pointant sur la bonne adresse locale. Ports Jenkins/Prometheus/Grafana liés à `127.0.0.1`, HTTPS Nginx optionnel et authentifié pour accès administrateur. L'audit local ne prouve pas à lui seul l'isolation Internet : contrôler également UFW, Docker et AWS Security Groups.

En cas de problème : **rester en mode `--check`**, examiner les journaux spécifiques, n'activer ni UFW ni redémarrage de service actif sans validation. Une modification de ports s'effectue via `/etc/autokom/observability.ini`, sans éditer les sources, et doit être validée avec les paramètres dynamiques.
