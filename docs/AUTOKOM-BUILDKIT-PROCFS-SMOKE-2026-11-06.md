# AUTOKOM · Correctif BuildKit rootless /proc + vérification réelle

**Base :** fichiers complets de la version AUTOKOM correspondant au commit serveur `74e4191968`, eux-mêmes issus du paquet de provisionnement du 6 novembre 2026. Installation protégée par empreintes exactes.

## Constat sur AWS Ubuntu 26.04.1

- Jenkins #6 exécute le job `autokom-client-deploy` et le clone GitHub ; `buildctl` télécharge les images `node` et `nginx`.
- `runc` échoue avant le lancement du shell de `RUN npm ci`, avec `error mounting "proc" ... operation not permitted`.
- Le noyau inscrit `VFS: Mount too revealing`.
- L'unité `autokom-buildkit.service` utilise `ProtectKernelTunables=yes`, qui remonte/masque des sous-arbres de `/proc`. Dans le namespace rootless, le nouveau montage procfs peut alors être refusé.
- La machine exécute déjà RootlessKit, le worker OCI et le socket privé. Les messages AppArmor fournis concernaient `who` et `ubuntu_pro_esm_cache`, pas directement BuildKit.

## Remédiation reproductible

1. `scripts/systemd/autokom-buildkit.service` désactive **uniquement pour ce service** `ProtectKernelTunables=`. Conserve `User=autokom-buildkit`, `ProtectSystem=full`, `ProtectKernelModules=true`, `ProtectControlGroups=true`, le socket UNIX et le réseau slirp4netns isolé.
2. `ansible/roles/buildkit_rootless/tasks/main.yml` déploie l'unité par copie versionnée, recharge systemd et redémarre uniquement ce service si modifié. Après le contrôle des workers, lance désormais un **vrai build OCI avec RUN**. Un échec interdit de déclarer l'installation opérationnelle.
3. `scripts/lib/autokom_buildkit_admin.py` réalise un build minimal sans code ni secret client, sous `autokom-builder`. Le Dockerfile est créé dans un répertoire temporaire privé, utilise `busybox:1.36.1` épinglé par le digest multiarch Docker officiel, ajoute un ARG aléatoire pour forcer RUN non caché, et n'exporte aucune image.
4. `autokom runtime doctor` vérifie aussi le vrai `RUN`, et non simplement l'existence des workers. Il ne modifie aucune configuration, mais consomme un peu de cache BuildKit et peut devoir télécharger l'image publique de contrôle lors du premier passage.

## Invariants de sécurité

- Ne jamais changer la valeur système `kernel.apparmor_restrict_unprivileged_userns` ; ne pas désactiver AppArmor.
- Ne pas ajouter `--oci-worker-no-process-sandbox`, `--privileged` ou une appartenance à `docker`.
- N'ajouter aucun port TCP exposé, aucun nouveau credential ou route API.
- Le backend communique toujours avec le broker via `/run/autokom/platform-control.sock`. Jenkins utilise son agent et le socket BuildKit privé `/run/autokom-buildkit/buildkitd.sock` ; le backend n'a **pas** accès à BuildKit.

## Séquence de mise à jour après validation sur Kali

```bash
# Kali : recopier via l'installateur fourni, tests, commit puis push.
# AWS : aucune commande apt, systemctl ou édition manuelle requise.
sudo autokom update
sudo autokom runtime buildkit --check
sudo autokom runtime buildkit --apply
sudo autokom runtime doctor
```

`autokom runtime agent --apply` appelle également le rôle BuildKit et impose désormais le test OCI au provisionnement des nouvelles machines. Le pipeline Django/Jenkins existant est conservé. Relancer une nouvelle publication client seulement si `runtime doctor` réussit.

## Limites de validation

Les tests Python et la syntaxe Ansible sont vérifiables sur Kali. Le succès réel du `RUN` sur noyau AWS `7.0.0-1006-aws` doit être validé sur le serveur. Si le test échoue encore, les sorties administrateur `journalctl -u autokom-buildkit` et le diagnostic filtré doivent guider la suite ; ne pas assouplir globalement les politiques de sécurité.
