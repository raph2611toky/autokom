# AUTOKOM — Integration 2D.2 : CI -> registre automatique, IAC HA, Prometheus/Grafana

## Ce que cette livraison fait reellement

- **Plus de migration manuelle des images** : apres `runtime.commit` du broker, une entrée de file root-only est creee. Le service `autokom-ha-autopublish.timer` publie dans Artifact Registry toutes les 2 minutes, avec reprises et digest immuable. Au premier cycle, il detecte aussi les releases locales deja actives et disposant encore d'un TAR Jenkins ; ne reconstruit pas des artefacts disparus.
- Les echecs de registre **ne cassent pas** les publications locales deja valides. Les images ne sont jamais reparties vers les VM web a cette etape.
- `autokom iac ha list` : instances, IP internes et externes, zone, etat, action, sante LB, CPU, trafic réseau reçu/envoyé, requêtes LB 5 min (metrics cloud disponibles seulement avec Monitoring API/permissions).
- `autokom iac ha logs --instance VM` : sortie série d'une instance du MIG (sans SSH).
- `autokom iac ha logs --source publisher` / `--source exporter` : journaux systemd privés.
- `autokom iac ha monitor` : santé réelle de la cible Prometheus et valeurs collectees ; `--check` simule ; `--apply` provisionne l'observabilite HA et active le publisher automatique. Si l'installation AUTOKOM de Prometheus/Grafana manque, l'installeur d'observabilite existant est réutilisé, sans deuxième stack.
- `autokom iac ha doctor` : diagnostic des workers et du LB + publication + Prometheus, statut applicatif jamais inventé.
- Grafana : dashboard `AUTOKOM · GCP HA · MIG / Load Balancer`, fourni automatiquement par un provider de fichiers dans le Grafana existant ; tableaux infra, CPU par VM, trafic par VM, codes 5xx, cibles et état des collecteurs. Les séries absentes restent **sans données**, jamais affichées à zéro.
- Prometheus : job autokom-gcp-ha + alertes sur collecte indisponible, moins de deux backends sains et erreurs 5xx.

## Limite fonctionnelle non négociable

Le code ne réalise **pas encore** le déploiement des images sur les VM web, leur reconciliation en cas de remplacement, la vérification du conteneur par réplique, l'attribution d'un backend de load balancing par application, ni le routage TLS des domaines clients. Il ne désactive donc **pas** les gardes HA en backend Django ; `autokom_ha_readiness_ok` reste à 0. Une application standard reste hébergée localement et son image peut être miroitee vers le registre. Le service de publication ne pousse aucun secret ni configuration des applications vers les workers.

## Installation

1. Depuis Kali : `unzip -o ARCHIVE.zip -d /home/raph/SOUTENANCE/AUTOKOM`, puis commiter les fichiers de l'archive.
2. Depuis le principal GCP : `sudo autokom update`.
3. Contrôler le plan : `autokom iac ha monitor --check`.
4. Activer l'intégration : `sudo autokom iac ha monitor --apply` (confirmer la passphrase si elle est demandée).
5. Vérifier :
   - `autokom iac ha list`
   - `autokom iac ha monitor`
   - `autokom iac ha doctor` (retour non nul normal tant que les applications clientes ne sont pas HA)
   - `sudo journalctl -u autokom-ha-autopublish.service -n 80 --no-pager`
   - `systemctl list-timers autokom-ha-autopublish.timer`
   - `curl -fsS http://127.0.0.1:19191/metrics`
   - `curl -fsS 'http://127.0.0.1:9090/api/v1/targets'` (port réel via paramètres d'observabilité)

## Sécurité et maintenance

Le service broker conserve les secrets clients et l'hébergement local intact. Seuls les **TAR Jenkins de chemins fixes** et les releases locales associees à un environnement/revision active peuvent être publiés. L'unité utilise l'identité de service GCE, jamais de JSON de clé, et une configuration Docker temporaire en mode 0700. La file root-only est durable ; un push échoué est réessayé. Les logs ne révèlent pas les tokens.

`monitor --apply` sauvegarde le fichier Prometheus avant changement, vérifie Prometheus avec `promtool`, refuse les conflits de jobs et les providers Grafana non gérés. Il n'utilise ni Terraform apply, ni migration de DNS, ni SSH/port entrant sur les VM clients. Toute image envoyée dans Artifact Registry consomme de l'espace facturable ; en absence d'artefact Jenkins local, une ancienne image ne peut pas être reconstituée automatiquement.

## Suite nécessaire pour une vraie HA applicative

Implémenter une configuration de déploiement désirée durable, l'exécution isolée des conteneurs sur chaque nouveau worker, la restauration après autoscaling, la gestion des secrets, le routage par domaine (backend service par application ou équivalent), HTTPS, et la validation de santé de **chaque** réplique. Le statut HA ne devra passer à 1 qu'après ces vérifications.
