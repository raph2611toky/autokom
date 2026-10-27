# AUTOKOM — Bannières CLI et validation sur AWS

Ce patch ne change ni les règles UFW, ni les Security Groups AWS, ni les
fichiers Nginx, ni les rôles Ansible. Les cinq bannières Python sont copiées
**sans changement** depuis les fichiers fournis.

## Intégration

- `scripts/lib/services.py` : logo Jenkins avant le menu Jenkins/BDD.
- `scripts/lib/jenkins_postinstall.py` : logo Jenkins au début du post-install.
- `scripts/lib/observability.py` : logos Jenkins/Prometheus/Grafana lors du
  `--check` ou de l'étape correspondante lors du `--apply`.
- `scripts/verify/09-check-stack.py` : audit en lecture seule avec logos.
- `scripts/banners/kibana.py` et `wazuh.py` : disponibles pour les futures phases,
  **sans installation** de ces outils.

Règles d'affichage : un logo par outil et par processus, seulement sur TTY.
`AUTOKOM_BANNERS=off` désactive tout ; `NO_COLOR=1` supprime les couleurs.
Sur terminal étroit, un titre compact remplace le grand dessin.

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
