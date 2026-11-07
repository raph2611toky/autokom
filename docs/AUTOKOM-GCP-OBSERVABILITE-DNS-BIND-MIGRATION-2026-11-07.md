# AUTOKOM — GCP : observabilité multicloud et migration du DNS BIND autoritaire

## Périmètre et règles

- VM GCP : `instance-20261028-090021` · `us-central1-a` · IPv4 externe actuellement `136.116.89.163` · IPv4 interne `10.128.0.4` (à confirmer).
- AUTOKOM détecte désormais les IPv4 externes GCP via le metadata server avec l'en-tête requis, ou AWS via IMDSv2. Le contrôle DNS Prometheus/Grafana ne présuppose plus EC2.
- AUTOKOM conserve `jenkins.ikom.mg`, les plugins installés, MySQL et les comptes Jenkins.
- AUTOKOM persiste les choix **non secrets** Prometheus/Grafana dans `/var/lib/autokom/install/observability-selection.json` (root:0600) après validation de la configuration. Pas de mot de passe dans ce fichier.
- La zone **racine `ikom.mg` reste autoritaire sur Cloudflare**. Seules les sous-zones BIND explicitement déléguées, par exemple `infra.ikom.mg`, sont migrées sur GCP. Ne pas recréer `ikom.mg` dans BIND.
- Les zones DNS gérées par une autre application ou manuellement ne sont jamais modifiées par l'import AUTOKOM. Export/import par zone AUTOKOM gérée uniquement.
- La migration DNS n'est **pas** déclenchée silencieusement par `autokom install` ; elle demande une décision et une confirmation de l'administrateur.

## 1. Migrer l'IP de GCP en IPv4 statique régionale

Depuis Kali, vérifier si l'IP est déjà réservée ; si elle est encore éphémère, la promouvoir :

```bash
gcloud compute addresses list --project=plucky-stacker-480421-c4 --regions=us-central1
gcloud compute addresses create autokom-gcp-ip \
  --addresses=136.116.89.163 \
  --region=us-central1 \
  --project=plucky-stacker-480421-c4
```

Ne lancer la seconde commande que si cette IPv4 n'est pas déjà statique.

## 2. Poursuivre Prometheus/Grafana

Sur GCP, après publication du correctif depuis Kali :

```bash
sudo autokom update
sudo autokom install --only observability
```

Configurer dans le terminal `prometheus.ikom.mg`, `grafana.ikom.mg`, leurs comptes/mots de passe de première installation, en conservant tes choix existants. Le programme vérifie l'IPv4 de l'instance via les métadonnées GCP. Les comptes techniques ne sont pas affichés dans les logs. Pour redemander les domaines volontairement :

```bash
sudo autokom configure observability
```

Ne pas changer les noms DNS si les enregistrements A Cloudflare pointent déjà vers GCP.

## 3. Inventorier le DNS autoritaire AWS AVANT export

Sur AWS, ne rien supprimer :

```bash
sudo autokom dns status
sudo autokom dns list
sudo autokom dns record list --zone infra.ikom.mg
sudo dig @127.0.0.1 infra.ikom.mg SOA +norecurse +noall +answer
sudo ls -l /etc/bind/named.conf.local /etc/bind/autokom/zones/
```

L'export automatique exige une zone primaire **gérée par AUTOKOM**, sa base JSON, son fichier BIND et sa clé TSIG conformes. En présence d'une zone BIND ancienne ou personnalisée qui n'est pas gérée par AUTOKOM, interrompre l'import automatique et sauvegarder d'abord les fichiers et zones externes spécifiques. Ne pas recréer une zone vide pour « faire passer » le contrôle.

Après publication de la nouvelle version sur AWS :

```bash
sudo autokom update
sudo install -d -m 700 /root/autokom-migration
sudo autokom dns migrate export \
  --zone infra.ikom.mg \
  --archive /root/autokom-migration/infra.ikom.mg.zip
sudo stat -c '%U:%G %a %s %n' /root/autokom-migration/infra.ikom.mg.zip
```

Le ZIP est **privé** et contient la clé TSIG ; ne pas envoyer dans un ticket, un dépôt Git ou un chat. Le faire transiter par SSH chiffré jusqu'à Kali, puis sur GCP avec `gcloud compute scp`, comme pour les fichiers `.env`. Ne pas utiliser de serveur HTTP public pour l'archive.

## 4. Installer BIND sur GCP et restaurer la zone

Sur GCP, une fois l'archive privée présente sous `/root/autokom-migration/infra.ikom.mg.zip` :

```bash
sudo install -d -m 700 /root/autokom-migration
sudo chmod 600 /root/autokom-migration/infra.ikom.mg.zip
sudo autokom dns install --check
sudo autokom dns install --apply
sudo autokom dns migrate import \
  --zone infra.ikom.mg \
  --archive /root/autokom-migration/infra.ikom.mg.zip
```

Examiner le plan, l'adresse publique détectée et l'adresse interne routée, puis appliquer :

```bash
sudo autokom dns migrate import \
  --zone infra.ikom.mg \
  --archive /root/autokom-migration/infra.ikom.mg.zip \
  --apply
```

L'import :

1. refuse une zone de destination déjà existante ;
2. valide les SHA-256 et la structure de l'archive ;
3. conserve les enregistrements source ;
4. promeut GCP comme primaire, rehausse le numéro SOA et retire une éventuelle désignation secondaire de la même VM ;
5. vérifie la syntaxe `named-checkzone` et `named-checkconf`, puis démarre/recharge BIND ;
6. ajoute de façon idempotente les autorisations UFW **UDP/TCP 53 sur l'interface publique de sortie** si UFW est actif, sans contourner un refus explicite.

L'import n'écrit **aucun** enregistrement Cloudflare et n'arrête pas AWS. L'IPv4 `136.116.89.163` est une adresse externe NAT GCP : BIND écoute sur l'IPv4 interne `10.128.0.4` (ou l'adresse de routage réellement détectée), **pas** sur l'adresse externe NAT.

### Pare-feu VPC GCP (distinct d'UFW)

Les règles VPC exigent les permissions IAM correspondantes. Depuis Kali :

```bash
gcloud compute instances add-tags instance-20261028-090021 \
  --project=plucky-stacker-480421-c4 \
  --zone=us-central1-a --tags=autokom-authoritative-dns

gcloud compute firewall-rules create autokom-authoritative-dns-53 \
  --project=plucky-stacker-480421-c4 \
  --network=default --direction=INGRESS --priority=1000 \
  --action=ALLOW --rules=udp:53,tcp:53 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=autokom-authoritative-dns
```

Inspecter les règles existantes avant de créer une nouvelle règle : ne pas la dupliquer si une règle équivalente existe déjà. **Ne pas ouvrir** les ports 8080, 3306, 9090 ni 3000.

## 5. Vérifier de l'extérieur avant la fin AWS

Sur Kali, et idéalement depuis un autre réseau :

```bash
dig @136.116.89.163 infra.ikom.mg SOA +norecurse +time=3 +tries=1
dig @136.116.89.163 infra.ikom.mg NS +norecurse +time=3 +tries=1
dig +tcp @136.116.89.163 infra.ikom.mg SOA +norecurse +time=3 +tries=1
dig @1.1.1.1 infra.ikom.mg SOA +short
```

Les réponses du serveur autoritaire doivent inclure le SOA de la zone, le nouveau serial et idéalement le flag `aa` dans l'en-tête. Vérifier aussi un enregistrement métier existant de la zone ; ne pas supposer qu'une zone vide suffit. En cas de doute ou d'absence de réponse, AWS reste actif et les données ne sont pas supprimées.

Dans Cloudflare, garder les NS de la zone racine `ikom.mg` et les A des applications web. Vérifier que `infra.ikom.mg NS ns1.ikom.mg` et `ns1.ikom.mg A 136.116.89.163` correspondent au service BIND GCP testé. Une seule autorité DNS est un point de défaillance unique : prévoir un secondaire indépendant avant l'arrêt définitif d'AWS. Les DS/DNSSEC, s'ils existent, doivent aussi être vérifiés.

## 6. Portée et limites

- Le correctif ne migre **pas** automatiquement les zones BIND externes qui ne figurent pas dans l'état AUTOKOM. Les sauvegarder séparément.
- Les données MySQL, Jenkins et Docker applicatifs sont une migration distincte : ces commandes DNS ne les transfèrent pas.
- Les identifiants root, les jetons, les mots de passe et les secrets TSIG ne figurent pas dans les journaux ou les captures.
- La vérification réseau et la conformité de chaque zone doivent précéder toute extinction de l'instance AWS.
