# AUTOKOM — Bannières CLI et validation sur AWS

Ce patch ne change ni les règles UFW, ni les Security Groups AWS, ni les
fichiers Nginx, ni les rôles Ansible. Les cinq bannières Python sont copiées
**sans changement** depuis les fichiers fournis.

## Intégration et politique finale (v2)

- `scripts/lib/services.py` : **grand logo Jenkins seulement** si le paquet est absent et que l'utilisateur a lancé `--apply`. Pas de grand logo en simulation ou pour un Jenkins existant.
- `scripts/lib/jenkins_postinstall.py` : **en-tête AUTOKOM compact uniquement**, y compris pour le premier administrateur, les tokens et plugins.
- `scripts/lib/observability.py` : grands logos Prometheus/Grafana uniquement à leur toute première installation effective. Jenkins n'a pas de grand logo dans la phase 3.
- `scripts/verify/09-check-stack.py` : **audit sans grand logo** ; en-têtes encadrés et résultats concis.
- `scripts/banners/kibana.py` et `wazuh.py` : ressources conservées pour les futures phases.

`AUTOKOM_BANNERS=off` interdit l'affichage des grands logos ; `NO_COLOR=1` utilise le rendu monochrome. Les grands dessins restent disponibles en aperçu manuel : `python3 scripts/lib/tool_banner.py grafana --preview`.

Voir également `docs/CLI-LIFECYCLE-VALIDATION.md` pour les scénarios, la sécurité et la procédure AWS.

## Procédure AWS progressive

```bash
sudo git -C /opt/autokom/source status --short
sudo git -C /opt/autokom/source pull --ff-only
cd /opt/autokom/source

# Contrôle sans modifier les services
sudo python3 scripts/verify/09-check-stack.py --scope all
sudo bash scripts/install/05-services.sh --check
sudo bash scripts/install/07-jenkins-postinstall.sh --check --interactive
sudo bash scripts/install/08-observability.sh --check --interactive
```

Ne pas procéder à `--apply` avant de sauvegarder les configurations et données
Jenkins, Nginx, Prometheus et Grafana. `--check` ne garantit pas qu'un `--apply`
fonctionnera sans erreur : la validation AWS est indispensable.

Après application **par étape** :

```bash
sudo nginx -t
sudo systemctl is-active jenkins prometheus grafana-server
sudo python3 scripts/verify/09-check-stack.py --scope all --strict
```

Les ports sont lus depuis `/etc/autokom/observability.ini`, sinon les valeurs
par défaut. L'audit contrôle l'écoute locale, la disponibilité HTTP, la cible
Jenkins dans Prometheus et le fichier de provisionnement Grafana. Il ne teste
pas les règles AWS Cloud : vérifier les Security Groups séparément.

Codes de retour de l'audit : `0` sans échec, `1` échec détecté, `2` configuration
invalide. Les services non encore installés sont des avertissements jusqu'à
l'activation du mode `--strict`.

**Ce patch n'a pas exécuté de commande sur ton instance EC2.**
