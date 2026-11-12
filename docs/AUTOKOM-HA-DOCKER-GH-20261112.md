# AUTOKOM — Correctif HA et GitHub CLI, 12 novembre 2026

## Origine du problème

Sur les workers GCP `autokom-ha-web-*`, `docker inspect` indique un
`HostConfig.PortBindings` pour `127.0.0.1:35309:8080/tcp`, mais
`NetworkSettings.Ports` est `null`. Un test local retourne `HTTP 000`, alors
que le conteneur répond `HTTP 200` sur son IP privée `172.18.0.2:8080`.

L'agent installe les containers sur un réseau Docker `--internal` et sonde
pourtant `127.0.0.1:<bind_port>` : les sondes échouent, les routes Nginx ne
sont pas validées et la réconciliation recrée les containers à répétition.

## Corrections incluses

- `terraform/modules/gcp_regional_ha/files/worker-agent.py` : garde la
  segmentation réseau `--internal`; n'utilise plus `--publish`; récupère l'IP
  Docker de **son seul bridge privé**; teste directement l'IP et le port interne;
  génère la cible `proxy_pass` correspondante; préserve les conteneurs qui
  fonctionnent, sans les supprimer sur simple échec de health check; trace la
  cause d'erreur sans secrets.
- `terraform/modules/gcp_regional_ha/files/worker-delivery-health.py` : la
  sonde santé HTTP utilise l'IP privée et le port interne conservés dans le
  fichier de routage. Le vhost applicatif transfère explicitement le chemin
  `/__autokom_health/` vers la sonde privée du worker.
- `scripts/lib/autokom_private_update.py` : accepte en option
  `AUTOKOM_UPDATE_GITHUB_USER=<login>`. Utilise `gh auth token --user <login>`
  sans modifier le compte actif de GitHub CLI, conserve le comportement
  existant de contrôle fast-forward, et n'enregistre aucun jeton GitHub dans
  le dépôt. Si la variable n'est pas définie, le Device Flow AUTOKOM continue
  exactement comme avant.

## Installer les fichiers dans le dépôt de développement

```bash
unzip -o /home/raph/Downloads/AUTOKOM-HA-DOCKER-GH-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
python3 -m unittest discover -s tests -p 'test_autokom_ha_internal_bridge_and_gh_20261112.py' -v
git diff --check
git status --short
```

Vérifier les modifications, puis committer et pousser normalement.
Sur le serveur principal : `sudo autokom update` (la première fois, via
le Device Flow historique tant que le compte `gh` n'est pas configuré).

### Connecter un deuxième compte `gh` sans déconnecter le premier

Sur le serveur principal, **dans l'environnement root** qui exécute AUTOKOM :

```bash
gh auth status
gh auth login --hostname github.com --git-protocol https --web
gh auth status
```

Choisir le compte GitHub personnel lors de la vérification de l'appareil.
GitHub CLI mémorise les deux identités. Pour conserver l'autre compte comme
compte actif au quotidien :

```bash
gh auth switch --hostname github.com --user ANCIEN_LOGIN
```

Éditer `/etc/autokom/update.env` en **préservant ses variables actuelles**.
Ajouter une ligne non secrète :

```ini
AUTOKOM_UPDATE_GITHUB_USER=MON_LOGIN_GITHUB
```

Puis exécuter `sudo autokom update`. AUTOKOM sélectionne directement le jeton
stocké pour ce nom, indépendamment du compte actif de `gh`. Les jetons sont
stockés par GitHub CLI, généralement dans le profil root ou le keyring local.
Ne pas afficher ni copier `gh auth token` dans un journal.

## Mise en service sur les workers GCP existants

La modification de fichiers Terraform **ne remplace pas** automatiquement
les scripts déjà présents sur les VM. Les instances MIG sont éphémères : il
faut aussi intégrer le nouveau template Terraform pour que chaque nouvelle
instance utilise ces sources après un déploiement/rolling update contrôlé.

Pour validation immédiate, copier les deux scripts modifiés sur **un seul**
worker, faire une sauvegarde des anciens fichiers, contrôler `py_compile`,
redémarrer uniquement les deux services AUTOKOM HA, vérifier la réconciliation,
puis répéter sur le second. Ne pas arrêter le serveur AUTOKOM principal.

Exemple pour le worker présent le 12/11/2026 :

```bash
PROJECT=plucky-stacker-480421-c4
ZONE=us-central1-a
WORKER=autokom-ha-web-5g1f
SRC=/opt/autokom/source/terraform/modules/gcp_regional_ha/files

gcloud compute scp "$SRC/worker-agent.py" \
  "root@$WORKER:/tmp/autokom-worker-agent-new.py" \
  --project="$PROJECT" --zone="$ZONE" --internal-ip

gcloud compute scp "$SRC/worker-delivery-health.py" \
  "root@$WORKER:/tmp/autokom-worker-health-new.py" \
  --project="$PROJECT" --zone="$ZONE" --internal-ip

gcloud compute ssh "$WORKER" --project="$PROJECT" --zone="$ZONE" --internal-ip
```

Sur la VM :

```bash
sudo python3 -m py_compile /tmp/autokom-worker-agent-new.py /tmp/autokom-worker-health-new.py
STAMP=$(date +%Y%m%d-%H%M%S)
sudo cp -a /usr/local/lib/autokom-ha/worker-agent.py "/root/worker-agent.$STAMP.bak"
sudo cp -a /usr/local/lib/autokom-ha/worker-health.py "/root/worker-health.$STAMP.bak"
sudo systemctl stop autokom-ha-worker-agent.service
sudo install -o root -g root -m 0644 /tmp/autokom-worker-agent-new.py /usr/local/lib/autokom-ha/worker-agent.py
sudo install -o root -g root -m 0644 /tmp/autokom-worker-health-new.py /usr/local/lib/autokom-ha/worker-health.py
sudo systemctl restart autokom-ha-worker-health.service
sudo systemctl start autokom-ha-worker-agent.service
sudo journalctl -u autokom-ha-worker-agent.service -n 35 --no-pager
sudo cat /etc/autokom-ha/routed.json
sudo nginx -t
```

Si l'application devient prête sur Web 1, répéter sur Web 2
(`autokom-ha-web-km01`, `us-central1-f`) avec les mêmes précautions.

La source de vérité pour les **nouvelles instances** reste le modèle Terraform
`google_compute_instance_template.web` : le startup template y injecte les
fichiers via `file()`. Vérifier un `terraform plan` avant tout `apply`.

## Validation applicative

```bash
HC='/__autokom_health/94ff7746-5db4-40ee-b3cd-6f2e6ea7e1d0/a789ba5a-f920-4e90-a624-a6da3d6088ca'
for ip in 10.128.0.15 10.128.0.12; do
  echo "$ip"
  curl -sS -H 'Host: ladyportfolio.auto.ikom.mg' \
    --max-time 8 -o /dev/null -w 'HTTP %{http_code}\n' "http://$ip:8080$HC"
done
sudo autokom iac ha apps -o wide
sudo autokom iac ha delivery --status
```

L'objectif est `2/2` saines, mais **la HA publique ne sera pleinement validée
qu'après mise en place du HTTPS/DNS LB et un test de basculement contrôlé**.
Ne pas retirer les conteneurs du serveur principal avant cette validation.
