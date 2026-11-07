# AUTOKOM — correction ciblée du conflit 443, UFW et HTTPS (7 novembre 2026)

## Faits observés sur l'instance GCP

- Interface WAN observée : `ens4` ; les règles UFW `DENY Anywhere on ens4` concernent uniquement les paquets entrants sur `ens4`, **pas** le loopback `lo` de `127.0.0.1`.
- Prometheus écoute sur `127.0.0.1:9090`, Grafana sur `127.0.0.1:3000` ; leur protection UFW sur `ens4` est voulue. Ne **pas** supprimer ces règles.
- Le processus `node`, PID 637, écoute `10.77.77.1:443`. Il peut s'agir d'un service VPN, mais son identité exacte n'est **pas** établie. Ne pas le stopper.
- Nginx écoute `0.0.0.0:80` et `[::]:80`, mais pas `443`, même si `nginx -t` répond `OK`. Une directive `listen 443 ssl;` crée un bind IPv4 global ; elle est incompatible avec la réutilisation simultanée de ce port sur `10.77.77.1`.
- Les certificats déjà obtenus et les virtual hosts Prometheus/Grafana ne prouvent pas que le port HTTPS fonctionne réellement.

## Correction

Répartir les sockets **sur les adresses locales exactes** :

- Nginx HTTPS : IPv4 privée routée de `ens4` (détectée automatiquement depuis `ip -j -4 route get 1.1.1.1`, `10.128.0.4` dans les journaux observés) **ET** `127.0.0.1` sur TCP/443.
- Processus `node` : `10.77.77.1:443` **inchangé**.
- Nginx HTTP : TCP/80 inchangé, les redirections HTTP vers HTTPS doivent répondre 301/308.
- Règles UFW, pare-feu VPC, SSH, comptes, données Prometheus/Grafana, certificats et Node.js inchangés.

Cette opération concerne toutes les directives IPv4 `listen 443 ssl` des fichiers actifs simples sous `/etc/nginx/sites-available`, `sites-enabled` et `conf.d`, y compris le vhost Jenkins si sa configuration correspond. Elle modifie ces fichiers **uniquement** après un `--apply` explicite. Le programme s'arrête si un listener concurrent occupe la même adresse, si la configuration Nginx comporte des directives ambiguës, ou si un fichier actif se situe hors des dossiers reconnus.

## Étape A : appliquer le correctif au dépôt sur Kali

Le patch est basé sur l'archive `AUTOKOM-SOURCES(1).zip`, commit `412a2267e29b8190ced058decd5b6e15ce032adf`. Il refuse de remplacer un fichier modifié depuis cette base. Depuis le dossier de l'archive patch :

```bash
python3 APPLIQUER_CORRECTIF.py --check /home/raph/SOUTENANCE/AUTOKOM
python3 APPLIQUER_CORRECTIF.py --apply /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_nginx_443_address_partition_20261107 tests.test_autokom_nginx_443_collision_recovery_20261107 tests.test_autokom_nginx_tools_reliable_20261107
git diff --check
git status --short
```

Si les tests sont conformes, commiter et pousser les fichiers corrigés vers le dépôt GitHub qui sert de source à `autokom update`. Ne pas écraser des changements locaux non intégrés au préalable.

## Étape B : réparation GCP depuis le dépôt mis à jour

```bash
sudo autokom update
sudo autokom tools nginx repair --check
sudo autokom tools nginx repair --apply
sudo ss -ltnp | grep -E '(:80|:443)[[:space:]]'
sudo nginx -t
sudo autokom tools prometheus status
sudo autokom tools grafana status
sudo autokom install --check
```

Si `/opt/autokom/source` a été modifié via `AUTOKOM_INSTALL_DIR`, identifier le chemin réel avec `readlink -f "$(command -v autokom)"`. Ne **pas** exécuter un `restart` manuel de Node.js ; la procédure contrôle un rechargement Nginx et un seul redémarrage Nginx si nécessaire, après sauvegarde.

### Critères de validation

1. `ss` : le processus `node` reste sur `10.77.77.1:443` ; Nginx écoute désormais sur les adresses `127.0.0.1:443` et IPv4 de `ens4:443`.
2. Depuis GCP, `curl -I --resolve prometheus.ikom.mg:443:127.0.0.1 https://prometheus.ikom.mg/` renvoie **401** sans identifiants (Basic Auth attendu) ; Grafana renvoie 200/302 ou 401 selon son état.
3. Depuis GCP, `curl -I -H 'Host: prometheus.ikom.mg' http://127.0.0.1/` renvoie **301 ou 308**, et pareil pour Grafana.
4. Depuis Kali / un autre réseau : `curl -4Ik --connect-timeout 5 https://prometheus.ikom.mg/` et `curl -4Ik --connect-timeout 5 https://grafana.ikom.mg/` ne doivent pas échouer par timeout / connexion refusée ; vérifier le certificat sans `-k` ensuite. Pour un diagnostic strict : `curl -4I --connect-timeout 5 https://prometheus.ikom.mg/`.
5. Si 1–3 réussissent mais 4 échoue, investiguer **seulement ensuite** GCP VPC, chemins DNS externes et UFW sur 443. Ne pas ouvrir directement 9090 ou 3000.

### Retour arrière

Avant de modifier le moindre fichier, le programme enregistre les sources exactes sous `/var/backups/autokom/nginx-443-<date>-<pid>/` avec un manifeste. En cas d'échec de `nginx -t`, d'écoute effective ou de HTTPS/SNI, il restaure automatiquement les fichiers Nginx et tente un reload. Si la restauration du processus ne peut pas être validée, il le signale et ne prétend pas que tout est réparé. Un retour arrière manuel doit être décidé à partir du manifeste, pas via un `cp` global qui pourrait écraser une mise à jour récente.

## Limites explicitement conservées

- Les tests locaux du code n'exécutent **pas** le correctif sur ta VM : le résultat réel dépend de ses fichiers Nginx et de l'identité exacte du processus Node.js.
- Le programme refuse de corriger les configurations Nginx non standard, incluant des directives `listen` complexes ou un autre port 443 sur la même adresse ; il n'arrête aucun processus étranger.
- Les fichiers `Nginx` déjà créés avec la nouvelle politique sont reconnus comme conformes. Les nouveaux générateurs AUTOKOM concernés réutilisent automatiquement la politique `/etc/autokom/state/nginx-https-bind.json` ; les configurations injectées par un outil externe (par exemple une migration manuelle Certbot) restent à vérifier.
- Ne pas ouvrir 9090, 3000, 9093 ou les ports de base de données sur Internet pour faire passer un test HTTPS.
