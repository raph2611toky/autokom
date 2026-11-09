# AUTOKOM · Installation des outils Infrastructure as Code

## Rôles distincts pour le mémoire de Master 2

La commande `autokom iac install` installe **Terraform**, puis **Google Cloud CLI**,
**sur le serveur principal Ubuntu** qui héberge l'orchestrateur AUTOKOM.
Elle ne crée aucune ressource GCP et ne lance pas `terraform apply`.

| Capture | Fichier à ouvrir sur Kali | Exécution sur Ubuntu |
| --- | --- | --- |
| Terraform — rôle d'installation | `ansible/roles/terraform_cli/tasks/main.yml` | `sudo autokom iac install --only terraform` |
| gcloud — rôle d'installation | `ansible/roles/gcloud_cli/tasks/main.yml` | `sudo autokom iac install --only gcloud` |
| Appel des rôles | `ansible/playbooks/iac-tools.yml` | `sudo autokom iac install --check` |
| Vérification des binaires | (aucun code supplémentaire) | `autokom iac status` |

## Commandes

```bash
sudo autokom iac install --check          # check Ansible, aucune modification
sudo autokom iac install                  # installe les deux outils
sudo autokom iac install --only terraform # capture Terraform uniquement
sudo autokom iac install --only gcloud    # capture gcloud uniquement
autokom iac status                        # versions locales
```

Sans Ansible sur le serveur, le mode application réutilise le bootstrap
`scripts/bootstrap/ansible.sh`. Le mode `--check` refuse de l'installer.
Si un exécutable Terraform ou gcloud est déjà installé et fonctionne,
le rôle correspondant n'impose pas une réinstallation.

**Sécurité et exploitation** : le provisionnement de l'infrastructure GCP devra
être piloté ultérieurement par AUTOKOM avec une identité de service dédiée,
des permissions IAM minimales et un état Terraform distant. Les comptes
personnels Kali ne sont pas copiés sur Ubuntu. Sur une VM Compute Engine,
l'authentification applicative peut utiliser le compte de service associé via
les Application Default Credentials (ADC), sous réserve des autorisations IAM
et des accès API disponibles.

Après l'installation des outils, la configuration GCP reste indépendante :
`terraform/environments/gcp-ha/`. Ne pas lancer `apply` sans audit du plan,
des quotas, des coûts et de l'état Terraform.
