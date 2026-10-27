> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Fondation (Ubuntu 26.04, serveur deja en production)

## Objectif
Installer **les commandes et outils de base** sans supprimer ni redemarrer les services existants.
Automatiser l'installation avec Ansible. Le module catch-all Nginx est separe et n'ecrase jamais des virtual hosts existants.

## Procedure

```bash
cd ~/AUTOKOM
bash scripts/bootstrap/preflight.sh
sudo bash scripts/bootstrap/ansible.sh
sudo bash scripts/install/foundation.sh --check
sudo bash scripts/install/foundation.sh --apply
sudo bash scripts/security/nginx-catchall.sh --check
# Seulement si aucun catch-all ni default_server ne sont deja presents :
# sudo bash scripts/security/nginx-catchall.sh --apply
bash scripts/verify/foundation.sh
```

## Sur le serveur observe
Nginx 1.28 et un catch-all `00-catchall.conf` actif ont ete observes.
La commande Nginx doit donc annoncer que la regle est deja en place, sans rien ecraser.
Les domaines applicatifs deja configures restent actifs.
Le domaine racine `ik0m.tech` n'etait pas configure dans la liste des vhosts et peut etre rejete, ce qui est normal si cela est souhaite.

## Securite et limites
- **Aucun** redemarrage de Docker ou Jenkins, aucune modification automatique de UFW, SSH, IAM, groupes de securite AWS/GCP.
- Ne lance pas automatiquement des scans Nikto ou Nmap contre des domaines externes.
- Des outils comme `nikto`, `nmap`, `lynis` peuvent manquer si les depots Ubuntu correspondants ne sont pas actives; le playbook le signale.
- La configuration du certificat Certbot et des domaines applicatifs sera dans le module reverse-proxy futur.
- Trivy est installe via son depot APT signe; Semgrep dans un venv Python dedie.
- `--check` est un mode de simulation Ansible, certains modules de telechargement ne peuvent predire tous les changements. Le mode effectif reste `--apply`.
