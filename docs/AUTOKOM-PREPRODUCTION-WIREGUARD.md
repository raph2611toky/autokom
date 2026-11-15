# AUTOKOM · Préproduction accessible uniquement via WireGuard

Le réglage est **désactivé par défaut**, y compris pour tous les projets déjà
déployés. Il s'applique à un environnement Préproduction isolé d'un projet à
un service. Dans la console dédiée `pre.auto.ikom.mg`, il est aussi proposé
pour l'environnement principal du projet, qui appartient à cette console
isolée. La console AUTOKOM, GitHub et la production restent publics ou privés
selon leurs propres réglages.

## Intégrer le correctif sur la version fournie

Le patch contient uniquement le delta, sans dépendances npm ni secrets.
Le paquet de livraison inclut aussi `AUTOKOM-PREPRODUCTION-WIREGUARD-fichiers.zip`,
une archive des seuls fichiers modifiés. Utiliser le patch ou cette archive,
pas les deux.
Depuis le dépôt local, sur une branche de travail au même état que
`autokom(4).zip` :

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git apply --check .\AUTOKOM-PREPRODUCTION-WIREGUARD.patch
git apply .\AUTOKOM-PREPRODUCTION-WIREGUARD.patch
git -c core.whitespace=cr-at-eol diff --check
git status --short
```

Si le dépôt stocke des fins de ligne LF là où le ZIP source en avait en CRLF,
réessayer le contrôle avec `git apply --check --ignore-space-change`. S'il
réussit, appliquer avec le même argument. Si le contrôle échoue encore,
ne pas utiliser `--reject` ou écraser la branche. Comparer le commit du dépôt
au ZIP de départ, ou intégrer les fichiers de l'archive ciblée.

Après revue et commit, publier le correctif sur la branche consommée par
l'instance (`master` pour `autokom-pre`, `main` pour AUTOKOM principal).
Ne pas appliquer les changements deux fois sur le même checkout.

## Préparer le tunnel sur la VM principale

Les prérequis sont `wireguard-tools`, `dnsmasq-base`, `nftables`,
`iproute2`, systemd et le module Nginx realip (fourni par le paquet Ubuntu
habituel). Installer seulement les paquets manquants si nécessaire.
Installer `dnsmasq-base`, pas le service global `dnsmasq`.

Le script crée une interface dédiée `akpreview`, en UDP **51896**, sur
`10.203.0.0/16`. Il refuse un conflit au premier démarrage. Il ne déplace
ni ne modifie une interface existante, dont `wg0`. Il crée sa propre
table nftables, sans toucher aux autres tables, et ne relance ni Docker ni
Nginx. Le seul nouveau port à autoriser depuis Internet est **UDP 51896**
vers cette VM. Ne pas ouvrir TCP 18443 ou DNS 53 sur Internet.

Si UFW est actif, l'accès de l'interface dédiée doit également être autorisé
sur le pare-feu hôte (le NAT a déjà converti 443 en 18443 avant le filtrage).

```bash
sudo ufw allow 51896/udp
sudo ufw allow in on akpreview to 10.203.53.1 port 18443 proto tcp
sudo ufw allow in on akpreview to 10.203.53.1 port 53 proto udp
sudo ufw allow in on akpreview to 10.203.53.1 port 53 proto tcp
```

Dans GCP, limiter la nouvelle règle UDP 51896 à la VM principale. Si
possible, limiter les sources aux réseaux clients connus. Ne pas cibler
les workers MIG. Si un pare-feu local recharge son jeu de règles avec
`flush ruleset`, conserver la table dédiée dans la configuration ou
redéclencher `sudo nft -f /etc/autokom-preview/firewall.nft` après ce
rechargement.

### Console de préproduction séparée

```bash
sudo autokom update preprod --check
sudo autokom update preprod
sudo bash /opt/autokom-preprod/source/scripts/wireguard/setup.sh
```

### Environnement Préproduction sur la console principale

Attendre la fin des exécutions avant de relancer le broker ou le worker.
Si le contrôleur HA tourne depuis un checkout séparé, y déployer aussi
ce correctif avant d'activer l'option.

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo systemctl restart autokom-platform-broker.service autokom-deployment-worker.service
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo bash /opt/autokom/source/scripts/wireguard/setup.sh
```

Dans la console isolée, les sous-domaines clients doivent rester dans
`AUTOKOM_PREPROD_ALLOWED_HOSTS`, comme pour une publication publique.

Une seule exécution de `setup.sh` par VM est nécessaire. Les deux profils
partagent le tunnel mais conservent des identités et des états séparés.

## Parcours client

1. Dans **Déployer → Configuration**, choisir **Préproduction**, puis cocher
   **Masquer la préproduction avec WireGuard**. Pour un site déjà déployé,
   ouvrir **Paramètres du projet → Accès à la préproduction**.
2. Lorsque la version est en ligne, cliquer sur **Télécharger le fichier
   WireGuard** et transmettre la configuration `.conf` par un canal privé.
3. Le client importe le fichier dans WireGuard, active le tunnel puis ouvre
   l'adresse HTTPS affichée. L'adresse du site ne change pas.
4. **Remplacer** génère un nouveau fichier et invalide l'ancien.
   **Révoquer l’accès** coupe le profil sans rendre le site public. Un seul
   profil actif est proposé par environnement.
5. **Rendre la préproduction publique** restaure l'accès sans VPN et révoque
   le profil. Aucun rebuild Docker n'est nécessaire pour changer la visibilité.

Le profil route seulement l'adresse de service du VPN (`10.203.53.1/32`)
et configure le résolveur du tunnel. Le reste du trafic n'est pas routé par
AUTOKOM. Un navigateur configuré avec un résolveur DNS-over-HTTPS strict
peut ignorer ce DNS. Pour le test, choisir le résolveur du système, vider le
cache DNS si nécessaire et réessayer.

## Contrôle avant remise au client

- Hors VPN, visiter l'URL depuis un réseau indépendant. L'application ne
  doit pas répondre, même en visant directement l'IP publique avec le même
  hostname. Nginx retourne HTTP 404. Si le DNS était encore en cache sur le
  load balancer, l'ancien chemin GCP est bloqué par une redirection vers un
  nom réservé .invalid avant que l'option ne soit confirmée.
- Dans WireGuard, vérifier le handshake et ouvrir la même URL. Le domaine
  doit se résoudre vers `10.203.53.1` et l'application doit répondre via
  HTTPS avec son certificat habituel. Les autres sites privés ne doivent
  pas répondre avec ce profil.
- Relancer un déploiement et faire une restauration. Refaire les tests
  hors et dans le VPN. Vérifier aussi un site public témoin.
- Révoquer le profil et vérifier que l'ancien fichier ne permet plus de
  charger le site. Puis créer un nouveau profil par le tableau de bord.

Exemple de contrôle DNS (sur un poste connecté au tunnel) :

```text
nslookup pre.MONPROJET.auto.ikom.mg 10.203.53.1
```

Les domaines et certificats publics restent en place pour la continuité
TLS et le retour au mode public. L'option rend **l'application** inaccessible
au public. Elle ne cache pas l'existence du nom DNS, ne retire pas une copie
publique du même dépôt ailleurs et n'efface pas les données déjà téléchargées.

Ne pas retirer le code du correctif tant qu'un environnement est en mode
privé. Si un retour à un ancien commit est nécessaire, révoquer d'abord
l'accès et retirer les publications privées ou choisir explicitement de
les rendre publiques. Les anciennes versions du broker ne connaissent pas
cette politique.
