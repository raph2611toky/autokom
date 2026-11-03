# AUTOKOM DNS — IKOM Entreprise

## Architecture conseillée (Cloudflare Free + AWS + GCP)

**Ne pas déclarer quatre NS différents de `ikom.mg` chez dot.mg.** Sur l'offre Cloudflare Free, les transferts de zone AXFR/IXFR vers BIND ne sont pas disponibles (fonction Enterprise). Des NS non synchronisés donnent des réponses DNS contradictoires.

- **Chez dot.mg :** communiquer *uniquement* les deux NS exacts attribués dans le tableau de bord Cloudflare lors de l'ajout de `ikom.mg`.
- **Cloudflare :** DNS autoritaire pour `ikom.mg`, sites publics, enregistrements A/AAAA/CNAME, MX/SPF/DKIM/DMARC, et délégation d'une zone.
- **BIND AWS (primaire) :** `ns1.ikom.mg` → IP publique AWS `56.228.86.160` (prévoir une Elastic IP fixe, car une IP EC2 éphémère peut changer).
- **BIND GCP (secondaire) :** `ns2.ikom.mg` → IP publique **statique** GCP (à renseigner quand le serveur sera disponible).
- **Sous-zone BIND :** `infra.ikom.mg`, déléguée par deux NS Cloudflare `infra NS ns1.ikom.mg` et `infra NS ns2.ikom.mg`.
- **DNS zone transfer :** BIND primary → secondary, TSIG HMAC-SHA256, NOTIFY et IXFR/AXFR. Pas de transfert Cloudflare en Free.

Cloudflare Free permet la délégation de sous-domaines vers des DNS externes : https://developers.cloudflare.com/dns/manage-dns-records/how-to/subdomains-outside-cloudflare/ .

## Installation dans AUTOKOM

Sur Kali, branche `main`, extraire le ZIP **depuis la racine du dépôt** :

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM_DNS_IKOM_Cloudflare_BIND_Direct.zip
python3 scripts/dev/activate_dns_cli.py --repo "$PWD"  # aperçu sans écriture
python3 scripts/dev/activate_dns_cli.py --repo "$PWD" --apply
python3 -m unittest discover -s tests -p 'test_autokom_dns.py' -v
python3 -m py_compile scripts/lib/autokom_cli.py scripts/lib/autokom_dns*.py

git add scripts/lib/autokom_cli.py scripts/lib/autokom_dns.py scripts/lib/autokom_dns_core.py \
  scripts/lib/autokom_dns_cloudflare.py scripts/dev/activate_dns_cli.py \
  ansible/playbooks/dns.yml ansible/roles/dns_server/tasks/main.yml \
  tests/test_autokom_dns.py docs/AUTOKOM-DNS-IKOM-MG.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat: DNS management Cloudflare and BIND9 primary/secondary"
git push origin main
```

Pourquoi la petite commande d'activation ? La CLI existante a beaucoup changé. Le ZIP est **directement dans l'arborescence Git**, mais écraser `autokom_cli.py` par une ancienne version casserait les commandes `app`, `admin` et `update`. Le script ajoute seulement trois sections, vérifie la syntaxe et est idempotent. Après le premier commit, `unzip` suffit pour les prochains changements.

## Sur AWS (serveur AUTOKOM déjà opérationnel)

```bash
sudo autokom update                       # passphrase AUTOKOM + GitHub Device Flow
sudo autokom dns -h
sudo autokom dns install --check          # simulation
sudo autokom dns install --apply          # installation via playbook Ansible
sudo autokom dns status
```

**Installation progressive — GCP pas encore prêt :** le primaire AWS peut être installé et testé avec un seul NS **uniquement en local**, sans délégation publique :

```bash
sudo autokom dns install --check   # syntax-check du playbook et du rôle
sudo autokom dns install --apply   # installer BIND9
sudo autokom dns setup --role primary --zone infra.ikom.mg \
  --environment production --primary-ip 56.228.86.160 --check
sudo autokom dns setup --role primary --zone infra.ikom.mg \
  --environment production --primary-ip 56.228.86.160 --apply
sudo autokom dns verify --zone infra.ikom.mg
```

`setup` sur un terminal interactif propose « Configurer AWS seul pour le moment » ou « J’ai déjà un serveur GCP disponible » avec le sélecteur AUTOKOM (flèches). Avec AWS seul, la zone ne contient que `NS ns1.ikom.mg`, `notify no`, `allow-transfer { none; }` ; aucune adresse fictive GCP ni délégation Cloudflare n'est créée. **Cette phase n'offre aucune redondance.** Elle ne doit pas être publiée comme délégation publique durable.

**Quand GCP sera prêt**, réserver son IP publique statique, installer BIND sur GCP, transférer sa clé TSIG hors Git, puis ajouter le secondaire sur AWS avec la même zone :

```bash
sudo autokom dns setup --role primary --zone infra.ikom.mg \
  --environment production --primary-ip 56.228.86.160 \
  --secondary-ip VRAIE_IP_PUBLIQUE_GCP --check
sudo autokom dns setup --role primary --zone infra.ikom.mg \
  --environment production --primary-ip 56.228.86.160 \
  --secondary-ip VRAIE_IP_PUBLIQUE_GCP --apply
```

Les enregistrements existants sont conservés, le serial SOA progresse, le transfert TSIG et NOTIFY sont activés. Vérifier que les deux NS répondent de manière cohérente *avant* de publier la délégation `infra.ikom.mg` dans Cloudflare. Sur GCP, utiliser ensuite `setup --role secondary` avec `--secondary-ip` obligatoire. `--listen-ip` est facultatif : AUTOKOM détecte l'adresse **locale de l'interface réseau** (en AWS/GCP, généralement privée derrière NAT). Ne pas configurer l'IP publique NAT comme adresse locale d'écoute : elle peut ne pas exister sur l'interface réseau.

Le setup valide BIND (`named-checkzone`, `named-checkconf`) et refuse les résolveurs déjà personnalisés incompatibles. Il désactive la récursion publique et configure un transfert TSIG, sans toucher à Nginx, MySQL, Docker ni au résolveur système `systemd-resolved`.

## Sur GCP (second serveur)

Installer AUTOKOM Core sur GCP avec Ansible. Réserver une adresse IP publique statique. Ouvrir UDP/TCP 53 depuis les réseaux concernés dans les pare-feu Google Cloud et système. Puis :

```bash
sudo autokom dns install --apply
```

Sur AWS primaire, AUTOKOM aura créé une clé TSIG sous `/etc/bind/autokom/keys/infra.ikom.mg.key`. **Transférer cette clé identique vers GCP avec SSH/SCP, hors du dépôt Git**, et installer sous le même chemin avec propriétaire `root:bind`, mode `0640` (répertoire parents accessible au groupe `bind`). L'outil de setup secondaire refuse de générer une nouvelle clé indépendante.

```bash
sudo autokom dns setup --role secondary --zone infra.ikom.mg \
  --environment production \
  --primary-ip 56.228.86.160 \
  --secondary-ip VRAIE_IP_PUBLIQUE_GCP \
  --apply
```

Vérifier depuis **un réseau extérieur**, pas seulement la machine AWS :

```bash
dig @56.228.86.160 infra.ikom.mg SOA +norecurse
# et dig @VRAIE_IP_PUBLIQUE_GCP infra.ikom.mg SOA +norecurse
```

N'installer la délégation dans Cloudflare **qu'après vérification** du SOA cohérent sur les deux serveurs. Les enregistrements `A` de `ns1.ikom.mg` et `ns2.ikom.mg` sont en mode DNS only (nuage gris).

## Cloudflare : gestion de ikom.mg (y compris e-mail)

Le compte Cloudflare se crée sur https://dash.cloudflare.com/ ; AUTOKOM ne crée pas automatiquement un compte Cloudflare sans autorisation utilisateur. Ajouter le domaine `ikom.mg`, choisir Free, copier **les deux noms NS réels** que Cloudflare affiche, et transmettre uniquement ces deux noms à dot.mg.

Pour que `autokom dns cloudflare` manipule les enregistrements de la zone : créer un API Token Cloudflare **restreint à `ikom.mg`** avec `DNS:Edit` (et lecture nécessaire à la liste). Garder le jeton **exclusivement** sur le serveur administrateur :

```bash
sudo install -d -m 700 /etc/autokom/secrets
sudo install -m 600 /dev/null /etc/autokom/secrets/cloudflare-dns.env
sudo nano /etc/autokom/secrets/cloudflare-dns.env
```

Contenu (à remplir localement, jamais sur GitHub) :

```dotenv
CLOUDFLARE_ZONE_ID=ZONE_ID_OBTENU_DANS_CLOUDFLARE
CLOUDFLARE_API_TOKEN=JETON_RESTREINT_A_IKOM_MG
```

Ne pas laisser d'exemple réel d'identifiants dans le dépôt. La clé TSIG BIND n'est pas le jeton Cloudflare et ne doit pas être confondue avec celui de la GitHub App.

Exemples :

```bash
sudo autokom dns cloudflare list
sudo autokom dns cloudflare delegation
sudo autokom dns cloudflare add --zone ikom.mg --type A --name ns1 --value 56.228.86.160 --apply
sudo autokom dns cloudflare add --zone ikom.mg --type A --name ns2 --value VRAIE_IP_PUBLIQUE_GCP --apply
sudo autokom dns cloudflare add --zone ikom.mg --type NS --name infra --value ns1.ikom.mg --apply
sudo autokom dns cloudflare add --zone ikom.mg --type NS --name infra --value ns2.ikom.mg --apply
```

Les MX, SPF, DKIM, DMARC sont dans la zone principale Cloudflare. Utiliser les valeurs **exactes de ton fournisseur mail**, pas des valeurs génériques. Pour un SPF ou DMARC, créer des enregistrements TXT ; pour MX, utiliser `--priority`. Exemple de syntaxe, sans valeur réelle :

```bash
sudo autokom dns cloudflare add --zone ikom.mg --type MX --name @ --value mail.FOURNISSEUR.TLD --priority 10 --apply
sudo autokom dns cloudflare add --zone ikom.mg --type TXT --name _dmarc --value 'v=DMARC1; p=none' --apply
```

**Attention :** ces lignes sont des exemples et ne doivent pas être exécutées tant que les informations du fournisseur mail ne sont pas connues. Les enregistrements DNS ne mettent pas en place un service de messagerie : il faut aussi héberger ou contractualiser le serveur SMTP/IMAP et gérer le PTR/rDNS chez l'hébergeur.

## Commandes BIND locales

```bash
sudo autokom dns                             # menu interactif avec flèches
sudo autokom dns list                        # zones gérées
sudo autokom dns environments                # étiquettes production / staging / development
sudo autokom dns status                      # état BIND
sudo autokom dns verify --zone infra.ikom.mg # réponses SOA locales / AWS / GCP
sudo autokom dns record list --zone infra.ikom.mg
sudo autokom dns record add --zone infra.ikom.mg --type A --name grafana --value 192.0.2.42 --check
# Remplacer par une IP réelle et utiliser --apply après validation.
```

Le mode `environments` est ici une **classification des zones** : il ne crée pas encore de `views` BIND réseau ou de sous-zones séparées automatiquement. Pour séparer staging/production en DNS public, choisir différentes sous-zones et les déléguer correctement.

## Sécurité et exploitation

- Le rôle BIND est chargé depuis `ansible/roles/dns_server` (chemin relatif au playbook ET `ANSIBLE_ROLES_PATH` injecté par la CLI). `install --check` effectue un vrai `ansible-playbook --syntax-check` sans modifier la machine.
- L'installation BIND est idempotente via `ansible/playbooks/dns.yml`, `policy_rc_d=101` empêche le démarrage anticipé du service par APT.
- Les opérations `--check` ne changent pas les fichiers.
- La CLI conserve les mécanismes AUTOKOM (`menu.pick`, passphrase et confirmations), pas de menu numéroté brut.
- La clé TSIG est sous `/etc/bind/autokom/keys` (root:bind 0640). Le jeton Cloudflare reste dans `/etc/autokom/secrets/` (root 0600). Aucun ne va dans Git.
- Les fichiers BIND générés sont validés avant chargement ; le contenu modifié est restauré en cas d'erreur de validation.
- Une option BIND déjà personnalisée ou une zone existante non gérée déclenche un refus, plutôt qu'un remplacement destructeur.
- TCP **et** UDP sur le port 53 doivent être autorisés par les pare-feu réseau AWS/GCP ET le pare-feu OS ; pas d'ouverture automatique.
- Prévoir surveillance SOA/NS, expiration du domaine et de l'IP publique, sauvegarde chiffrée hors site des fichiers TSIG et des zones.
- Pour publier des services clients AUTOKOM, ne confondez pas `ikom.mg` avec les anciens domaines `ik0m.tech` (chiffre zéro dans `ik0m`). Ce patch n'effectue aucune migration des URL applicatives.

Références : https://developers.cloudflare.com/dns/zone-setups/zone-transfers/ , https://developers.cloudflare.com/dns/manage-dns-records/how-to/subdomains-outside-cloudflare/ , https://ubuntu.com/server/docs/how-to/networking/install-dns/ .

## Incident Ansible rôle introuvable — résolu

L'ancien `dns.yml` utilisait `role: dns_server` alors que le rôle se trouve dans `ansible/roles/dns_server/`, et Ansible ne recherche par défaut que `ansible/playbooks/roles/` pour ce playbook. AUTOKOM fournit maintenant le chemin relatif et l'environnement `ANSIBLE_ROLES_PATH`. Il n'est pas nécessaire de copier le rôle dans `/root/.ansible/roles`, de modifier les rôles des autres modules ou de réinstaller AUTOKOM.

Le premier `dns install --apply` a échoué avant l'installation des paquets : ne pas supposer que BIND a été installé. Le prochain `dns install --check` doit indiquer `Playbook Ansible et rôle dns_server vérifiés` ; l'installation effective se fait ensuite avec `--apply`.

**Ne pas communiquer `ns1.ikom.mg` à dot.mg comme unique NS.** Transmettre à dot.mg seulement les deux NS exacts du tableau Cloudflare pour la zone principale `ikom.mg`. `ns1.ikom.mg` seul reste un serveur de test de la sous-zone `infra.ikom.mg` jusqu'à la mise en service du secondaire.
