# AUTOKOM · Préproduction WireGuard autonome

Le réglage est désactivé par défaut. Les sites publics continuent à utiliser
leur chemin habituel (DNS public, certificat et, le cas échéant, haute
disponibilité). Le mode WireGuard est limité aux environnements de
préproduction à un service et aux sous-domaines AUTOKOM autorisés.

## Mettre à jour l'installation existante

Après publication de cette version sur `main`, la console principale se met
à jour avec le parcours standard, puis la commande intégrale publie Django,
le broker, le worker et React. Aucun paquet VPN n'est installé par une
mise à jour publique sans demande de préproduction privée.

```bash
sudo autokom update && sudo autokom app deploy --full
```

La console isolée `pre.auto.ikom.mg` suit toujours `master`. Lorsque ce
commit est sur cette branche, son mécanisme habituel
`sudo autokom update preprod` installe aussi le point d'entrée du
provisionneur. Ne pas mélanger les configurations des deux consoles.

## Depuis AUTOKOM

Dans Déployer ou Modifier la configuration, choisir Préproduction et
cocher **Masquer la préproduction avec WireGuard**, puis enregistrer.
Pour une préproduction déjà en ligne, le même réglage est disponible
dans ses paramètres sans rebuild. Le navigateur affiche l'avancement et
reprend automatiquement après un conflit transitoire vérifié.

À la première demande, le service dédié installe les outils manquants,
prépare `akpreview`, configure dnsmasq, les règles locales et le
renouvellement TLS. Sur GCP, il utilise l'identité de la VM pour vérifier
ou créer l'autorisation UDP 51896 ciblant cette seule instance. Il ne
modifie ni le MIG ni `wg0`, ni les règles d'autres ports. Il conserve
les clés et réutilise les services existants. Le service de provisionnement
est déclenché à la demande, et ne bloque pas le démarrage général
d'AUTOKOM si les prérequis WireGuard sont indisponibles.

Le contrôle GCP utilise la configuration et les droits existants. Sans
autorisation suffisante pour lire le réseau ou créer sa règle dédiée,
le système signale `AUTOKOM_PREVIEW_CLOUD_IAM`. Il ne s'attribue pas
de permissions et ne contourne pas les politiques du projet. Le mode
local ne modifie pas les pare-feu de fournisseurs non GCP.

Un site privé n'a pas besoin d'un enregistrement A public pour être
publié. Certbot émet le certificat via DNS-01 dans la zone gérée, puis
AUTOKOM vérifie le DNS du tunnel, la réponse HTTPS et le refus d'accès sur
l'ingress public. Un ancien enregistrement public peut rester en place,
mais le domaine privé n'est pas servi sur le chemin public. Pour revenir
en mode public, AUTOKOM crée automatiquement le DNS manquant et attend
sa propagation avant de retirer la protection. Le client peut ensuite
télécharger son profil secret, l'importer et vérifier le site depuis son
poste. Le remplacement ou la révocation du profil ne republient rien.

## Vérification opérationnelle

- Après une publication privée réussie, vérifier l'URL depuis un réseau
  externe avec puis sans le tunnel, et vérifier un site public témoin.
- La première publication peut attendre les dépendances serveur et le
  certificat. Le journal de `autokom-preview-provision.service` conserve
  un diagnostic sans clé privée ni jeton.
- Une préproduction privée n'est pas une copie du site de production
  rendue privée rétroactivement. Le site de production garde sa propre
  visibilité.
- Ne pas revenir à un broker qui ne comprend pas la politique privée
  tant que des projets l'utilisent.
