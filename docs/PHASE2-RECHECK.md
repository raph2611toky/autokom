> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

# AUTOKOM — Phase 2, reprise sur serveur existant

Correctif des interactions TTY, vérification des configurations effectives et conservation des services existants.

## Problème corrigé

`open('/dev/tty','r+')` utilise des buffers texte bidirectionnels et peut lever `File or stream is not seekable` sur un terminal. Le menu lit et écrit maintenant exclusivement avec `os.open`, `os.read`, `os.write` sur `/dev/tty`.

## Bind ≠ autorisation

- `--bind local` : écoute `127.0.0.1`.
- `--bind private` : écoute toutes interfaces avec au moins un CIDR client déclaré, **mais la politique pare-feu doit encore être configurée**.
- `--bind all` : écoute toutes interfaces IPv4 (`0.0.0.0`) ; **n'accorde pas automatiquement** de droit SQL aux clients `@'%'`.
- `--allow 172.18.0.0/16` : autorise le compte applicatif AUTOKOM depuis ce réseau IPv4. Choisir explicitement le(s) CIDR des ponts Docker réellement utilisés.
- `--allow 0.0.0.0/0` : compte applicatif global ; exige la confirmation supplémentaire `ACCES-PUBLIC` (ou `--allow-public-clients` en non-interactif). **Déconseillé.**

## Cas déjà installés

- MySQL : vérifie `@@GLOBAL.bind_address`. Si identique à l'adresse demandée, pas de fichier `.cnf` écrit, pas de redémarrage. Si différent, l'override propre à AUTOKOM est écrit dans `zz-autokom.cnf` (après `mysqld.cnf`), validé, redémarré après confirmation puis contrôlé. En cas d'échec, tentative de restauration.
- PostgreSQL : vérifie `SHOW listen_addresses` sur le cluster principal avant de modifier. Ne redémarre que si l'écoute doit changer ; les règles du rôle applicatif sont gérées séparément.
- Jenkins : conserve les comptes, tâches et domaines existants ; vérifie l'écoute du port 8080, privilégie `127.0.0.1`, et demande une approbation avant un redémarrage d'une instance active.
- Nginx/Certbot : si un vhost existe et n'est pas géré par AUTOKOM, il n'est pas remplacé ; `nginx -t` est contrôlé, mais le statut fonctionnel du proxy et du TLS est à vérifier séparément.
- Oracle Free : refuse de considérer un conteneur existant comme conforme s'il est arrêté ou si son port n'est pas local.

## Tests ciblés

```bash
python3 tests/test_phase2.py
bash -n scripts/install/services.sh
python3 -m py_compile scripts/lib/menu.py scripts/lib/services.py
```

Sur **serveur AWS** :

```bash
sudo mysql --protocol=socket -uroot -NBe 'SELECT @@GLOBAL.bind_address;'
sudo ss -lntp '( sport = :3306 )'
sudo mysql --protocol=socket -uroot -NBe "SELECT user,host FROM mysql.user WHERE user='autokom_app';"
sudo systemctl cat jenkins
sudo ss -lntp '( sport = :8080 )'
sudo nginx -t
sudo bash /opt/autokom/source/scripts/install/services.sh --check
```

Appliquer **seulement après simulation et sauvegarde** :

```bash
sudo bash /opt/autokom/source/scripts/install/services.sh --apply
```

Pour exécution non interactive, ajouter `--non-interactive --db mysql --bind all --unsafe-all --allow 172.18.0.0/16 --docker-access no --domain ''` et `--confirm-restart` uniquement pendant une fenêtre de maintenance.

Les Security Groups et les ACL réseau AWS/GCP ne sont pas modifiés par ces scripts ; ouverture `0.0.0.0` au niveau du service peut accroître sa surface d'exposition, même avec les permissions SQL restreintes.

La création d'une base `autokom_apps` et d'un compte `autokom_app` est désormais **facultative**. En menu, choisir « Non, conserver les bases existantes » ; en non-interactif, le comportement par défaut est de ne créer aucun compte SQL. Ajouter `--create-app-db` seulement si cette base d'exemple est souhaitée.

Une installation Jenkins déjà active peut nécessiter un redémarrage si elle écoute sur `*:8080` ; le module demande une confirmation explicite. Un service MySQL existant avec `@@GLOBAL.bind_address = 0.0.0.0` ne doit pas redémarrer pour ce motif.
