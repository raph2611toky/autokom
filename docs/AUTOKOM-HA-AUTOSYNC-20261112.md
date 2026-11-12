# AUTOKOM — Synchronisation automatique et sûre des workers HA

**Périmètre :** projet GCP `plucky-stacker-480421-c4`, région `us-central1`, MIG `autokom-ha-rmig`.

**Objectif :** éviter d'exécuter `gcloud compute scp` / `ssh` et de redémarrer les services à la main à chaque mise à jour d'AUTOKOM. Le serveur principal conserve les applications actuelles pendant la validation de la HA ; aucun DNS, Load Balancer, conteneur client ou Terraform n'est détruit par ce correctif.

## Fichiers ajoutés / modifiés

- `scripts/lib/autokom_ha_worker_sync.py` : inventaire MIG dynamique, synchronisation des deux workers un à un, compte de service GCP déjà autorisé, accès IP privée, verrou contre exécutions concurrentes, idempotence et installation d'un timer systemd.
- `scripts/lib/autokom_ha_worker_remote_apply.py` : application atomique des fichiers sur chaque VM, contrôle syntaxique et SHA256, sauvegarde de l'ancienne version, restauration automatique si installation/services échouent, aucun changement si sources identiques.
- `scripts/lib/autokom_private_update.py` : après une mise à jour du code, synchronise les workers *si* l'automatisation a déjà été activée **et** si les scripts HA ont changé. Le reste du comportement de mise à jour GitHub est conservé.
- `tests/test_autokom_ha_worker_sync_20261112.py` : tests non destructifs.

Les scripts worker effectivement installés sont issus de la version de dépôt `dc81578e3e` ou ultérieure :

- `terraform/modules/gcp_regional_ha/files/worker-agent.py`
- `terraform/modules/gcp_regional_ha/files/worker-delivery-health.py`

Ces deux derniers fichiers ne sont pas dupliqués dans cette archive, puisqu'ils sont **déjà présents dans le dépôt** après le correctif HA Docker précédent.

## Installation dans le dépôt local

```bash
unzip -o /home/raph/Downloads/AUTOKOM-HA-AUTOSYNC-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
python3 -m unittest discover -s tests -p 'test_autokom_ha_worker_sync_20261112.py' -v
git diff --check
git status --short
```

Commiter puis pousser les fichiers vérifiés. Sur le GCP principal :

```bash
sudo autokom update
```

**La première mise à jour qui installe ce patch n'active pas encore le timer**. C'est volontaire : l'autorisation d'un accès SSH automatisé doit être un choix explicite de l'administrateur.

## Activation unique sur le serveur GCP principal

```bash
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --check
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --install-timer
sudo systemctl list-timers --all autokom-ha-worker-sync.timer
```

Cette activation ne redémarre **que** les services d'agent et de santé sur chaque worker dont les fichiers ont changé ; elle ne redémarre ni Nginx du serveur principal, ni les applications déployées sur celui-ci. Les workers sont traités successivement, et une VM en cours de remplacement est ignorée jusqu'au passage suivant.

`--install-timer` installe un timer exécuté environ toutes les 15 minutes. Le script récupère **la liste actuelle** des membres du MIG : une instance recréée ou ajoutée par l'autoscaler sera prise en charge au prochain passage une fois `RUNNING` et `currentAction=NONE`. Si la synchronisation échoue, le timer recommencera ; consulter les journaux.

L'exécution utilise une clé SSH Ed25519 dédiée à l'automatisation (sous `/var/lib/autokom/ha-worker-sync`, dossier root en mode 0700 et clé privée en mode 0600), **sans passphrase**, pour qu'un timer puisse l'utiliser. Les clés installées dans les métadonnées GCP expirent après 30 minutes (`--ssh-key-expire-after=30m`). Ce compromis suppose que l'accès root du serveur principal est correctement protégé. Aucune clé privée, aucun jeton GitHub ni credential ne sont intégrés dans le dépôt.

Le script **n'ouvre aucun port**, ne crée aucune règle de pare-feu, n'utilise pas IAP et ne met à jour que les membres du MIG explicitement indiqué. Il dépend de la règle VPC préexistante `autokom-ha-allow-control`, qui autorise le port 22 depuis `10.128.0.4/32` vers `autokom-ha-backend`.

## Utilisation courante

```bash
# Inventaire et validation locale uniquement
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --check

# Mise à jour immédiate de tous les workers (sans intervention sur les VM)
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --apply

# Journal de l'automatisation et prochain lancement
sudo journalctl -u autokom-ha-worker-sync.service -n 50 --no-pager
sudo systemctl list-timers --all autokom-ha-worker-sync.timer

# Vérification applicative
sudo autokom iac ha apps -o wide
sudo autokom iac ha delivery --status
```

Pour suspendre l'automatisation :

```bash
sudo systemctl disable --now autokom-ha-worker-sync.timer
```

Le timer actif ne suffit **pas** à prouver la HA : attendre `2/2 HEALTHY` et vérifier le trafic du Load Balancer. `enable_https=false` et `domain_name=null` sont des points distincts à traiter dans une phase de bascule publique contrôlée.

### Si l'agent reste UNHEALTHY

Ne pas supprimer les conteneurs, le MIG, ni les applications du serveur principal. Après l'installation, patienter jusqu'au prochain cycle de réconciliation, puis consulter uniquement les **nouveaux** journaux :

```bash
sudo journalctl -u autokom-ha-worker-sync.service --since '15 minutes ago' --no-pager
sudo autokom iac ha apps --diagnose
```

Si nécessaire, sur la VM worker :

```bash
sudo journalctl -u autokom-ha-worker-agent.service --since '10 minutes ago' --no-pager
sudo cat /etc/autokom-ha/routed.json
```

En cas d'échec lors d'une mise à jour, le script distant restaure les deux scripts sauvegardés sous `/var/lib/autokom-ha/code-backups/`. L'application client continue de fonctionner sur le serveur principal pendant cette opération.

## GitHub CLI : aucun besoin de reconnecter les comptes

`gh auth status` montre déjà `raph2611toky` **et** `Jennylady`. La première commande `autokom update` qui a installé `dc81578e3e` s'exécutait encore avec l'**ancien** module : il était normal qu'elle demande un code Device Flow.

Après mise à jour, le module utilise le compte nominatif **uniquement si** la configuration existante contient :

```ini
AUTOKOM_UPDATE_GITHUB_USER=raph2611toky
```

Vérifier sans afficher de token :

```bash
sudo grep '^AUTOKOM_UPDATE_GITHUB_USER=' /etc/autokom/update.env
sudo gh auth status
```

Une fois cette ligne présente, le compte `gh` utilisé pour `autokom update` peut être sélectionné **sans modifier le compte actif**. Ne pas utiliser `gh auth logout`, ne pas copier de token dans `update.env`, ne pas modifier le remote Git pour y insérer des secrets.

## Limites et sécurité

- Testé localement avec mocks ; **pas encore exécuté contre ton compte GCP réel** depuis cet environnement.
- L'automatisation SSH s'appuie sur l'accès déjà démontré au projet GCP et aux workers, et sur un compte Linux `root`. Des politiques OS Login ou IAM modifiées peuvent nécessiter une adaptation.
- Une nouvelle instance créée depuis un ancien template Terraform exécute initialement l'ancien agent, puis reçoit la version corrigée au prochain cycle du timer. Pour éliminer cette fenêtre, appliquer ultérieurement un plan Terraform validé qui incorpore les nouveaux fichiers dans le template, avec politique de rolling update appropriée. Ce patch ne force pas ce renouvellement.
- Le dossier root contenant la clé non chiffrée est sensible. Restreindre l'accès root, auditer les clés dans les métadonnées GCP et ne pas sauvegarder ce dossier dans des artefacts accessibles aux utilisateurs.
