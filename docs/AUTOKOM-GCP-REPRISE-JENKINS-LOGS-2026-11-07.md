# AUTOKOM — Reprise sécurisée de Jenkins, journaux et loader (7 novembre 2026)

## Incident ACME observé

Les journaux GCP du 7 novembre 2026 montrent la validation HTTP-01 de
`jenkins.ik0m.tech` résolue successivement en `56.228.86.160` (ancien AWS,
port 80) puis `136.116.89.163` (nouveau GCP, port 443). La connexion TCP
au serveur GCP sur HTTPS a expiré (`Timeout during connect`). Le fait que
AWS possède un certificat pour le même FQDN n'empêche pas GCP d'en obtenir
un. Il faut corriger la cohérence DNS publique et l'accès externe au port 443
avant une nouvelle tentative, sans supprimer le certificat AWS.

AUTOKOM ne modifie ni le DNS public ni les pare-feu GCP sans décision de
l'administrateur. Sur Kali, vérifier :

```
dig @1.1.1.1 +short A jenkins.ik0m.tech
dig @8.8.8.8 +short A jenkins.ik0m.tech
gcloud compute instances describe instance-20261028-090021 \
  --project=plucky-stacker-480421-c4 --zone=us-central1-a \
  --format='get(tags.items)'
gcloud compute firewall-rules list \
  --project=plucky-stacker-480421-c4 \
  --format='table(name,network,direction,allowed,sourceRanges,targetTags,disabled)'
```

Confirmer les règles GCP **et** UFW pour TCP/80 et TCP/443. Ne jamais
exposer le port 8080 Jenkins ni 3306 MySQL à Internet.

## Paramètres enregistrés

Lors de la première exécution de cette version, après validation des
choix administratifs, le module enregistre uniquement les options non
secrètes (moteur SQL, interface d'écoute, autorisations CIDR choisies,
création d'un compte applicatif, accès Docker demandé, domaine Jenkins,
mode TLS, email ACME et chemins de certificats) :

- `/var/lib/autokom/install/services-selection.json` — fichier root, mode 0600 ;
- `/var/lib/autokom/install/services.incomplete` — une installation interrompue reste à reprendre ;
- `/var/lib/autokom/install/services.complete` — créé uniquement après la
  réussite de la configuration Jenkins, BDD et proxy HTTPS.

Aucun mot de passe, jeton GitHub, clé privée, consentement de redémarrage
ou sortie brute de commandes n'est sauvegardé dans le JSON. Les secrets
applicatifs existants restent dans `/etc/autokom/secrets`.

Les relances de `sudo autokom install` réemploient les choix sans redemander
les formulaires. Pour demander **volontairement** une nouvelle configuration :

```
sudo autokom configure services
# ou
sudo autokom install --only services --reconfigure
```

Le mode reconfiguration conserve la vérification et les confirmations
nécessaires avant toute nouvelle interruption des services actifs.

Comme les anciennes versions ne mémorisaient pas les choix, la **première
exécution après installation du correctif** peut nécessiter de renseigner
les choix une dernière fois. Ensuite la reprise est automatique.

## Loader, étapes et journaux

Les commandes `systemctl restart`, `systemctl reload` et `certbot` affichent
un loader interactif sans afficher de faux pourcentage. En parallèle, leurs
transitions `running / succeeded / failed`, heure UTC, composant et code de
retour sont conservées sans arguments ni secrets dans :

`/var/log/autokom/install/services-AAAAMMJJTHHMMSS-PID.jsonl` (mode 0600)

`sudo autokom install` affiche le chemin correspondant à chaque exécution.
Le journal détaillé Ansible demeure dans `/var/log/autokom/` et l'historique
Certbot dans `/var/log/letsencrypt/letsencrypt.log`. Les sorties systemd
sont accessibles avec `sudo journalctl -u jenkins -u mysql -u nginx`.

Cette correction ne prétend pas ouvrir les règles de pare-feu GCP,
mettre à jour DNS, ou garantir une délivrance ACME avant résolution de
l'incident réseau. Ne pas multiplier les tentatives ACME en cas de DNS
public contradictoire ou d'accès HTTPS bloqué.
