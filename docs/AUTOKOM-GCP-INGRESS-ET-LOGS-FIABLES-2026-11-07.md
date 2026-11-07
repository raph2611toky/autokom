# AUTOKOM · GCP — Ingress Nginx, vérifications réelles et diagnostics

## Problème constaté le 7 novembre 2026

Les phases de configuration ont réussi, Certbot Cloudflare DNS-01 a délivré les certificats, mais l'ouverture de `http://prometheus.ikom.mg` affichait la page Ubuntu « Welcome to nginx! ». Depuis la VM, un `curl https://prometheus.ikom.mg` vers l'IP **publique** échouait. Les états de services indiquaient néanmoins « conformes ». Les commandes `autokom tools prometheus logs`, `autokom tools grafana logs` et `autokom tools status` étaient absentes.

Cela démontre qu'**un processus actif n'est pas une preuve de disponibilité publique**. La VM GCP utilise son adresse privée sur l'interface; l'IPv4 publique est traduite par Google Cloud. Tester l'IPv4 publique depuis la même VM peut donc échouer même quand le serveur HTTPS local est correct. Une vérification externe distincte est indispensable.

## Ce correctif ajoute

1. **Catch-all Nginx** : seul le site Ubuntu standard, reconnu dans `/etc/nginx/sites-enabled/default`, peut être désactivé automatiquement. Son fichier `/etc/nginx/sites-available/default` reste sur disque. Le nouveau `00-autokom-catchall.conf` renvoie **404** sur HTTP pour les noms non reconnus et refuse le handshake TLS pour les noms inconnus. L'ancien catch-all AUTOKOM `444` est accepté et préservé s'il est déjà configuré. Un `default_server` personnalisé est conservé et bloque cette modification automatique : aucune suppression aveugle.
2. **Application transactionnelle** : `nginx -t`, suppression du seul lien standard, création exclusive de la configuration, `nginx -t` et rechargement, puis restauration du lien si validation ou rechargement échoue. Ne touche ni aux proxy spécifiques Jenkins/Prometheus/Grafana, ni aux certificats ni à l'utilisateur Grafana.
3. **Contrôle de disponibilité réel** : `HEAD /` avec `Host` HTTP et un vrai SNI + vérification CA/nom sur HTTPS, **vers les adresses locales** `127.0.0.1` et celle de la NIC routée. Attendus : Prometheus HTTPS 401 (auth Basic), Grafana 200/3xx/401, redirection HTTP 301/308. Un HTTP 200 qui correspond au « Welcome to nginx » n'est plus une réussite.
4. **Plan idempotent** : les noms des domaines proviennent des choix privés déjà sauvegardés par AUTOKOM, même si le fichier `observability.ini` n'a pas été mis à jour. La conformité est refusée lorsque le proxy HTTP/HTTPS est incorrect ou que le catch-all manque. La phase d'observabilité se réexécute alors sans réinstallation inutile des services.
5. **Diagnostic par outil** : `autokom tools status`, `autokom tools prometheus status`, `autokom tools grafana status`, `autokom tools nginx status`, `autokom tools prometheus logs --lines 80`, `autokom tools grafana logs -f`, `autokom tools nginx logs --lines 80`, `autokom tools certbot logs --lines 50`, etc. Les journaux sont en lecture seule, plafonnés à 1000 lignes par appel hors suivi, les commandes n'utilisent pas de shell, les secrets usuels sont masqués au mieux. Toujours relire les logs avant de les partager, des données d'application peuvent y figurer.
6. **Pare-feu** : UFW est inventorié avec les règles HTTP/HTTPS; **le pare-feu VPC GCP n'est jamais supposé ouvert** sur la seule base d'UFW. Ce correctif ne crée pas de règle GCP en l'absence de droits/projet connus et ne modifie pas les règles explicitement refusées pour 9090, 3000 et 8080.

## Sur Kali — tests et publication

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-NGINX-INGRESS-HEALTH-LOGS-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_nginx_tools_reliable_20261107 \
  tests.test_tools_v9 \
  tests.test_autokom_cli \
  tests.test_observability \
  tests.test_autokom_cloudflare_dns01_certbot_20261107

git diff --check
git add config/nginx/00-catchall.conf scripts/lib/autokom_ingress_health.py \
  scripts/lib/autokom_tools_diagnostics.py scripts/lib/autokom_cli.py \
  scripts/lib/tools_registry.py scripts/lib/observability.py \
  scripts/security/nginx-catchall.sh \
  tests/test_autokom_nginx_tools_reliable_20261107.py \
  docs/AUTOKOM-GCP-INGRESS-ET-LOGS-FIABLES-2026-11-07.md
git commit -m "fix: reconcile GCP ingress and expose tool status/logs with real HTTPS checks"
git push origin main
```

## Sur GCP — reprise

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
sudo autokom tools status
sudo autokom tools nginx status
sudo autokom tools prometheus status
sudo autokom tools grafana status
sudo autokom tools nginx logs --lines 60
sudo autokom tools prometheus logs --lines 50
sudo autokom tools grafana logs --lines 50
```

Les commandes `status` retournent un code non nul quand un état fonctionnel attendu manque; elles ne redémarrent rien. La phase d'installation, elle, peut recharger Nginx après validation de la configuration et conserve les comptes/secrets.

## Vérification externe et VPC — depuis Kali, pas depuis l'instance GCP

```bash
curl -4sSI --connect-timeout 6 --max-time 12 http://prometheus.ikom.mg/
curl -4sSI --connect-timeout 6 --max-time 12 https://prometheus.ikom.mg/
curl -4sSI --connect-timeout 6 --max-time 12 https://grafana.ikom.mg/

# Vérifier les règles VPC réellement appliquées à la NIC GCP.
gcloud compute instances network-interfaces get-effective-firewalls \
  instance-20261028-090021 --project plucky-stacker-480421-c4 \
  --zone us-central1-a --network-interface nic0 --format=json
```

Attendu pour HTTP Prometheus : `301/308` vers HTTPS; pour HTTPS Prometheus : `401` sans identifiants; pour Grafana HTTPS : interface de connexion/redirection. Le retour de **Nginx générique** n'est pas une réussite, même avec code 200. Les tests locaux ne remplacent pas les tests Kali ni l'inspection du VPC. Ne pas exposer directement les ports privés 9090, 3000, 8080, 9100, 9093.

## Limites et garde-fous

- Une configuration Nginx personnalisée avec son propre `default_server` ne sera pas remplacée. Les détails sont accessibles via `sudo nginx -T`, `sudo autokom tools nginx logs`.
- Le site Nginx d'origine est conservé dans `/etc/nginx/sites-available/default`; seul le lien standard est désactivé, puis restauré si le reload échoue. Un rollback manuel ne doit être effectué qu'en évitant tout conflit de `default_server`.
- Le code de validation et les tests locaux n'ont pas accès à la VM ni au VPC du demandeur : l'accessibilité internet et le comportement réel après `autokom install` restent à confirmer.
- Les certificats de Prometheus/Grafana et les comptes Jenkins/MySQL/Grafana ne sont ni supprimés ni renouvelés par cette correction s'ils sont déjà valides.
