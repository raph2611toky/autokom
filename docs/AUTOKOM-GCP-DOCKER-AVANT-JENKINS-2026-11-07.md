# AUTOKOM GCP — Docker avant Jenkins, reprise de la phase 2

## Défaillance constatée

Le programme `sudo autokom install` exécutait la fondation avec les rôles `base_cli`
et `security_cli` mais aucun rôle Docker. Il installait ensuite Jenkins, dont
le rôle `jenkins_server` appelait `docker info` après l'installation du paquet.
Sur GCP Ubuntu 24.04, le client Docker était absent, avec
`[Errno 2] No such file or directory: b'docker'`. La reprise était bloquée
par un autre défaut : Jenkins déjà actif suffisait à marquer `services` comme conforme.

## Correctif

- La fondation appelle désormais le rôle Ansible `docker_engine` **avant** le module Jenkins.
- L'appel direct à `services.yml` exécute aussi ce rôle **avant** `jenkins_server`.
- Sur une machine vierge, AUTOKOM prépare le dépôt HTTPS officiel Docker signé par `signed-by`, puis installe Docker Engine, CLI, containerd.io, Buildx et Compose, démarre Docker et vérifie `docker info`.
- Sur un hôte avec un Docker fonctionnel, le rôle ne modifie pas les paquets,
  services, conteneurs, volumes ou réseaux Docker. Si des paquets Docker/containerd
  étrangers sont détectés sans client Docker utilisable, la conversion automatique
  est refusée plutôt que de supprimer ces paquets.
- Le plan d'installation considère Docker prêt uniquement si son démon répond.
- Une installation Jenkins interrompue est repérée lorsque l'override AUTOKOM
  est présent mais que l'initialisation de son URL ne l'est pas encore.
  L'installateur reprend le module complet et demande les choix administratifs.
- Un marqueur de phase n'est créé qu'après la réussite de la configuration Jenkins,
  bases SQL et reverse proxy/TLS, jamais après la seule installation du paquet.

## Reprise sur GCP

Intégrer et pousser le correctif depuis Kali, puis sur GCP :

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
sudo autokom status
```

Conserver le domaine Jenkins `jenkins.ik0m.tech`, MySQL, la configuration du
pare-feu et les confirmations d'accès Docker demandées par l'administrateur.
Ce correctif ne déplace ni ne restaure les données AWS. Ne pas basculer
Cloudflare ou arrêter AWS avant la validation des services GCP.

## Limites

Les tests statiques et unitaires ne remplacent pas une installation réelle
APT/Docker sur GCP. Docker peut créer des règles iptables indépendantes d'UFW :
continuer à limiter les publications de ports, les règles GCP et `DOCKER-USER`.
L'installateur ne purge aucune distribution Docker existante.
