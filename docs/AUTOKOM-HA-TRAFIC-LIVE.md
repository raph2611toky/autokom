# AUTOKOM · Observer la répartition de charge par application

L'instrumentation lit les journaux d'accès du Nginx de chaque worker GCP via le SSH privé déjà utilisé par le synchroniseur HA. Elle ne génère aucun trafic, ne modifie pas les domaines et ne nécessite ni nouvelle IP publique ni application Terraform. Le déploiement et les pipelines clients ne sont pas relancés.

## Installer

Extraire `AUTOKOM-HA-TRAFIC-LIVE.zip` à la racine du dépôt AUTOKOM. Commiter seulement ces fichiers et pousser sur la branche utilisée par le contrôleur.

```bash
git diff --check
git add -- docs/AUTOKOM-HA-TRAFIC-LIVE.md scripts/lib/autokom_cli.py scripts/lib/autokom_ha_admin.py scripts/lib/autokom_ha_traffic.py terraform/modules/gcp_regional_ha/files/worker-agent.py tests/test_autokom_ha_traffic.py tests/test_autokom_ha_internal_bridge_and_gh_20261112.py
git commit -m "feat: stream HA request distribution per application"
git push origin main
```

Sur la VM de contrôle, `sudo autokom update` déclenche la synchronisation séquentielle des workers si le timer HA de synchronisation est installé. Vérifier la sortie, puis vérifier les commandes.

```bash
sudo autokom update
sudo systemctl list-timers --all autokom-ha-worker-sync.timer
sudo autokom iac ha app portfoliolady.auto.ikom.mg logs --lines 20
```

Si le timer n'a jamais été installé, l'activer une fois depuis la VM de contrôle.

```bash
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --install-timer
```

Après la synchronisation, laisser le service worker appliquer son premier manifeste (cycle de 35 s si les images sont déjà saines). Aucun événement antérieur à l'activation des nouveaux journaux n'est reconstitué.

## Observer le trafic

Avant une mesure publique, vérifier que **le domaine ciblé** est `PUBLIC_HTTPS_ACTIVE` et que les deux réplicas sont sains. Le bilan global peut être positif seulement pour un autre projet.

```bash
sudo autokom iac ha delivery --status
sudo autokom iac ha apps --ha-only
sudo autokom iac ha app portfoliolady.auto.ikom.mg follow --seconds 180
sudo autokom iac ha app portfoliolady.auto.ikom.mg logs --lines 300
sudo autokom iac ha app portfoliolady.auto.ikom.mg logs --all
```

`follow` affiche toutes les deux secondes le débit observé sur la fenêtre écoulée, le compteur cumulé, sa part du total et les HTTP 5xx par worker. Ajouter `--requests` pour afficher aussi chaque ligne. Les workers sont sélectionnés à l'ouverture dans le dernier rapport de placement et doivent exister dans le MIG. Relancer le suivi après une modification de placement.

`logs` affiche l'historique HTTP retenu dans les workers du MIG actuel, y compris les anciens placements encore présents. `--lines` lit le fichier courant de chaque worker et trie les lignes par date. `--all` affiche aussi les rotations numérotées encore disponibles, y compris compressées, dans l'ordre de lecture de chaque worker. Les logs d'une VM supprimée ne sont pas conservés par cette commande. Il s'agit des **accès HTTP au proxy du worker**, pas des sorties du code applicatif, ni des journaux Jenkins.

Les sondes internes sont exclues. Les journaux affichent l'heure, la méthode, le code HTTP, la durée, le volume et un marqueur de test facultatif. Ils n'incluent pas les URL, paramètres, IP clientes, en-têtes d'authentification ou cookies. Un worker marqué indisponible n'est pas un worker à zéro requête.

## Benchmark à lancer plus tard

Depuis la VM de contrôle, ouvrir le suivi filtré et attendre « Suivi prêt ».

```bash
sudo autokom iac ha app portfoliolady.auto.ikom.mg follow --seconds 180 --tag akbench-demo01
```

Depuis une autre machine, sur le site AUTOKOM autorisé, commencer par un palier bref à 50 req/s, puis passer à 200 et 500 req/s uniquement si les résultats restent bons. Ne pas superposer les paliers.

```bash
rate=50
printf 'GET https://portfoliolady.auto.ikom.mg/\nX-Autokom-Bench: akbench-demo01\n\n' |
  vegeta attack -rate="$rate/s" -duration=15s -timeout=3s |
  vegeta report
```

Relancer le suivi entre paliers pour des compteurs indépendants. À 500 req/s pendant 15 s, le générateur tente environ 7 500 requêtes. Comparer le débit effectivement atteint, le taux de succès, le p95 et le p99, ainsi que le total reçu par chaque worker. Si les deux compteurs augmentent, les deux réplicas ont servi des requêtes. Un ratio 50/50 n'est pas une condition de réussite. Si le total client diffère du total worker, vérifier notamment la fenêtre de suivi, les erreurs avant le backend et un éventuel cache en amont.

Variante ApacheBench pour un test à concurrence fixe, sans débit imposé.

```bash
ab -n 1000 -c 20 -k -s 3 -H 'X-Autokom-Bench: akbench-demo01' https://portfoliolady.auto.ikom.mg/
```
