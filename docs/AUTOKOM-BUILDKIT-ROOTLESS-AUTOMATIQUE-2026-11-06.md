# AUTOKOM — Provisionnement BuildKit rootless automatique

## Périmètre

Ce correctif s'ajoute au **patch de prévol déjà appliqué mais non commité** sur `d22380a`.
Il ne modifie pas le frontend, Nginx, les DNS, le broker ou Docker existants.
Il modifie le parcours `autokom runtime agent --apply` (rôle BuildKit exécuté après le rôle Jenkins), et ajoute `autokom runtime buildkit --check|--apply`.

## Source et sécurité

- BuildKit **v0.34.2** officiel ; archive AMD64/ARM64 HTTPS, SHA-256 figés et vérifiés via `get_url` Ansible avant extraction.
- Les outils restent dans `/opt/autokom/toolchains/buildkit/v0.34.2/bin`, root-owned. Aucune version globale de `buildctl` n'est écrasée.
- Daemon UNIX non privilégié `autokom-buildkit`, distinct du compte `autokom-builder`. Socket `/run/autokom-buildkit/buildkitd.sock` (pas de TCP, propriété du socket contrôlée automatiquement par systemd après démarrage (groupe `autokom-builder`)).
- Sous-UID et sous-GID automatiquement alloués, sans conflit avec les plages existantes.
- RootlessKit + slirp4netns avec `--disable-host-loopback`. Snapshotter `native` choisi pour sa compatibilité ; performances à mesurer.
- Pas de groupe Docker, pas de socket Docker monté, pas de `--privileged`.
- Moteur BuildKit réutilisé et vérifié par `buildctl debug workers` sous la vraie identité Jenkins.
- Le socket BuildKit demeure une API privilégiée dans son **identité non root** : seuls les jobs AUTOKOM approuvés sont admis au groupe ; cela ne constitue pas un bac à sable matériel pour du code inconnu.

## Kali : modifications en un seul commit

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
# Le patch précédent BuildKit_Preflight doit être appliqué mais PAS encore commité.
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_BuildKit_Rootless_Autoprovision_AFTER_Preflight.patch
git apply /home/raph/Downloads/AUTOKOM_BuildKit_Rootless_Autoprovision_AFTER_Preflight.patch
python3 -m unittest -v tests.test_autokom_buildkit_preflight tests.test_autokom_buildkit_autoprovision tests.test_autokom_groovy_sandbox
git diff --check
git diff --stat
git add \
  ansible/playbooks/buildkit-rootless.yml \
  ansible/playbooks/jenkins-agent.yml \
  ansible/roles/buildkit_rootless/tasks/main.yml \
  scripts/systemd/autokom-buildkit.service \
  scripts/systemd/autokom-jenkins-agent.service \
  scripts/lib/autokom_buildkit_admin.py \
  scripts/lib/autokom_buildkit_socket_permissions.py \
  scripts/lib/autokom_buildkit_subids.py \
  scripts/lib/autokom_jenkins_agent.py \
  scripts/lib/autokom_runtime_admin.py \
  scripts/runtime/autokom_agent_local.py \
  tests/test_autokom_buildkit_preflight.py \
  tests/test_autokom_buildkit_autoprovision.py \
  docs/AUTOKOM-BUILDKIT-PREFLIGHT-2026-11-06.md \
  docs/AUTOKOM-BUILDKIT-ROOTLESS-AUTOMATIQUE-2026-11-06.md
git commit -m "feat: provision isolated rootless BuildKit automatically"
git push origin main
```

## AWS Ubuntu : application

Faire un backup des fichiers d'unité et vérifier qu'aucune exécution client n'est active. Puis :

```bash
sudo autokom update
sudo autokom runtime buildkit --check
sudo autokom runtime buildkit --apply
# taper PROVISIONNER
sudo autokom runtime agent --check
sudo autokom runtime agent --apply
# taper CONNECTER, redémarre seulement le service d'agent et provisionne si nécessaire
sudo autokom runtime doctor
sudo systemctl status autokom-buildkit autokom-jenkins-agent --no-pager
```

Sur une installation AUTOKOM neuve, la seule commande `runtime agent --apply` installe aussi BuildKit. Sur le serveur existant, `runtime buildkit --apply` permet une réparation ciblée sans reconfigurer GitHub ni Jenkins.

## Bloqueurs sûrs

Ubuntu peut restreindre les user namespaces non privilégiés via AppArmor. Ce correctif n'abaisse **pas** les protections globales ; en cas de refus, consulter `journalctl -u autokom-buildkit -n 80 --no-pager`, et prévoir une politique AppArmor minimale auditée spécifique à BuildKit. Ne pas désactiver AppArmor ou donner l'accès à Docker pour contourner un échec.

Le déploiement de clients reste subordonné à un worker BuildKit disponible : l'API Django/Unix/Jenkins existante n'est pas modifiée. La commande doctor doit désormais échouer si BuildKit est indisponible, avant tout nouveau déploiement client.
