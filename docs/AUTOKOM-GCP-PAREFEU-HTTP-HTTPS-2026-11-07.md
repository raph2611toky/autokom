# AUTOKOM — Ports HTTP/HTTPS publics et pare-feux lors de l'installation

## Incident constaté sur GCP le 7 novembre 2026

Jenkins et MySQL étaient opérationnels, Cloudflare pointait `jenkins.ikom.mg` vers
`136.116.89.163`, mais Let's Encrypt HTTP-01 échouait par `Timeout during connect`
sur TCP/80. Sur GCP, `ufw status` indiquait seulement `443/tcp on wg0 ALLOW` et
aucune autorisation de 80/443 sur l'interface publique `ens4`. Les ports 8080 et
3306 restaient volontairement bloqués en WAN.

## Comportement de l'installation corrigée

Lorsque l'administrateur choisit un domaine public :

- HTTPS via Certbot, certificat fourni ou déjà existant : ouverture ciblée de
  TCP/80 et TCP/443 sur l'interface WAN détectée via `ip -4 route get`.
- HTTP sans TLS : ouverture de TCP/80 uniquement.
- Pas de domaine : aucune nouvelle autorisation WAN 80/443.
- UFW actif : AUTOKOM crée seulement les règles absentes, puis relit `ufw show added`.
- UFW inactif : la protection SSH existante avec confirmation explicite reste
  nécessaire avant toute activation du pare-feu.
- Une règle DENY explicite sur le même port WAN provoque un refus clair au lieu
  d'être écrasée silencieusement.
- Les interdictions TCP/8080, 3306, 2375, 2376, etc. et les interfaces `wg0`,
  `docker0` restent respectées.
- Un journal d'installation privé enregistre `firewall:host`, sans exposer de
  secrets ou d'arguments sensibles.

Ce contrôle se déroule **avant Jenkins/Certbot**. Un nouveau lancement est
idempotent, y compris après un échec Let's Encrypt.

## Pare-feu Google Cloud : seconde couche distincte

**Un programme dans la VM ne peut pas déduire qu'une règle VPC GCP autorise le
trafic Internet en examinant seulement UFW.** Le système doit disposer de droits
IAM Compute pour modifier le pare-feu VPC ; ne jamais accorder à l'instance une
permission d'administration projet sans analyse du risque.

Vérification depuis le poste administrateur Kali déjà authentifié à GCP :

```bash
PROJECT=plucky-stacker-480421-c4
ZONE=us-central1-a
INSTANCE=instance-20261028-090021

gcloud compute instances describe "$INSTANCE" \
  --project="$PROJECT" --zone="$ZONE" \
  --format='yaml(tags.items,networkInterfaces[0].network)'

gcloud compute firewall-rules list --project="$PROJECT" \
  --format='table(name,network,direction,priority,allowed,sourceRanges,targetTags,disabled)'
```

Si les tags de l'instance correspondent déjà à une règle autorisant **uniquement**
`tcp:80,tcp:443`, ne rien créer. Sinon, l'administrateur GCP doit autoriser une
règle ingress ciblée sur cette VM (par tag ou compte de service). Exemple de
création ciblée après vérification du réseau et des règles préexistantes :

```bash
# A exécuter sur Kali uniquement si cette règle est réellement absente.
gcloud compute instances add-tags "$INSTANCE" \
  --project="$PROJECT" --zone="$ZONE" \
  --tags=autokom-public-web

gcloud compute firewall-rules create autokom-public-web-ingress \
  --project="$PROJECT" --network=default \
  --direction=INGRESS --action=ALLOW \
  --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 \
  --target-tags=autokom-public-web
```

Ces commandes ne doivent jamais ouvrir TCP/8080, 3306, 2375 ou 2376.
De même, aucune modification ne doit être faite sur les autres règles du VPC.
Une règle hiérarchique DENY de priorité supérieure nécessite un contrôle séparé.

## Contrôles

```bash
sudo ufw status verbose
sudo ss -ltnp '( sport = :80 or sport = :443 )'
dig @1.1.1.1 +short A jenkins.ikom.mg
dig @8.8.8.8 +short A jenkins.ikom.mg
sudo autokom status
```

La disponibilité TCP **depuis Internet** doit aussi être confirmée ; une requête
vers l'IP publique depuis la VM elle-même ne suffit pas. Let's Encrypt HTTP-01
nécessite l'accès au port 80 depuis ses validateurs.
