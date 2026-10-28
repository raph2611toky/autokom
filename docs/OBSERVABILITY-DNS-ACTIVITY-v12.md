# AUTOKOM — Correctif Observabilité v12 : écoute, activités, domaines

Ce patch cible `scripts/lib/observability.py` sans changer les paramètres
actuels, le cœur `autokom install`, la stratégie UFW ni les comptes existants.

## Écoute Jenkins

- Partage `jenkins_network.private_listener()` avec l'installateur principal.
- Reconnaît `127.0.0.1:8080`, `[::1]:8080` et `[::ffff:127.0.0.1]:8080`.
- Rejette `*:8080`, `0.0.0.0:8080`, `[::]:8080` et les écouteurs mixtes.
- Ne redémarre pas Jenkins si l'écoute est déjà conforme.

## Indicateurs d'activité

Le rendu compact `Activity` existant est désormais utilisé pendant les
attentes silencieuses : vérification DNS/IP EC2, vérification de TCP/80,
redémarrage/attente Jenkins, démarrage Grafana, datasource Grafana et attente
de la cible Prometheus `UP`. Les commandes de paquets, systemd, Nginx et
Certbot disposent déjà du panneau d'activité temporaire de `run()`.

Le loader n'apparaît **pas** pendant un prompt (touche / menu / secret), afin
de ne pas perturber la saisie. Il ne laisse pas d'ANSI dans les logs ou pipes.

## Précontrôle DNS et HTTPS

Lorsqu'un nouveau domaine est demandé, AUTOKOM vérifie avant les secrets et
les modifications :

1. Nom de domaine syntaxiquement valide (3 tentatives de saisie).
2. IPv4 publique de l'instance via IMDSv2 (jamais IMDSv1).
3. DNS A exactement égal à cette IPv4 ; aucun A divergent.
4. DNS AAAA éventuel assigné réellement à une interface publique du serveur.
5. Pour un nouveau certificat HTTP-01, serveur Nginx accessible en **local**
   sur 127.0.0.1:80. Aucun port n'est ouvert automatiquement.

Les configurations avec ALB, CDN ou un proxy de terminaison TLS externe ne
sont pas couvertes par ce profil **EC2 direct** et seront explicitement
refusées si le DNS ne pointe pas vers l'instance.

Si IMDSv2 est indisponible, l'administrateur peut fournir une IP publique
explicitement (`--public-ip X.X.X.X`) au module d'observabilité ; si IMDSv2
fonctionne, une IP manuelle contradictoire est rejetée.

**Limite importante** : un test local TCP/80 ou DNS **ne prouve pas** que
le service est accessible depuis Internet. Vérifier les règles entrantes du
Security Group, NACL, UFW et le routage. Pour HTTP-01, la validation ACME par
Let's Encrypt/Certbot est la vérification externe définitive. Une
configuration non vérifiée ne doit pas être présentée comme publiquement
accessible.

## Installer et reprendre

Sur Kali : appliquer le ZIP à la racine AUTOKOM, lancer les tests, puis
`git add scripts/lib/observability.py scripts/lib/domain_validation.py tests/test_observability_domain_activity_v12.py docs/OBSERVABILITY-DNS-ACTIVITY-v12.md`.

Sur AWS : `sudo autokom update` puis `sudo autokom install --check`, puis
`sudo autokom install` seulement après contrôle du réseau. Les services
conformes restent ignorés. Ne pas désactiver le pare-feu pour contourner une
erreur DNS ou HTTP-01.

Tests locaux = appels DNS, IMDSv2, systemd et HTTP simulés ; valider ensuite
la connectivité réelle et le certificat sur l'instance EC2.
