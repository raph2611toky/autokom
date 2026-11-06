# AUTOKOM — diagnostic BuildKit pour la publication b56b6da9

Base ciblée : `d22380a21e` (2026-11-06). Ce correctif ne prétend pas installer BuildKit ni corriger un échec de compilation sans connaître son origine exacte.

## Constat vérifié

- Jenkins #5 a récupéré le dépôt `raph2611toky/portfolio` au SHA `7d408176...`.
- Les scans Semgrep et Trivy ont été ignorés : le mode DevOps ne lance pas les scans DevSecOps.
- `autokom_agent_local.py build` a appelé `buildctl` puis a retourné le code 2 en masquant la vraie catégorie d'erreur.
- Le playbook `ansible/roles/jenkins_agent/tasks/main.yml` ne provisionne pas explicitement `buildctl` / `buildkitd` ni un daemon rootless ; le service `autokom-jenkins-agent.service` ne définit pas `BUILDKIT_HOST`.

## Correctif

- Prévol avant la compilation : client `buildctl` disponible, `BUILDKIT_HOST` de type socket UNIX, chemin lié au compte builder, socket présent et connectable.
- Refus explicite du socket rootful partagé (`/run/buildkit/buildkitd.sock`) et des endpoints TCP.
- Erreurs classées dans les marqueurs `AUTOKOM_LOG` déjà pris en charge par le backend (aucune sortie brute ni secret ajouté).
- Aucune modification du droit d'accès aux sockets Docker, du reverse proxy ou de Jenkins.

## Intégration Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM_BuildKit_Preflight_d22380a_2026-11-06.zip -d /home/raph/Downloads/
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_BuildKit_Preflight_d22380a_2026-11-06.patch
git apply /home/raph/Downloads/AUTOKOM_BuildKit_Preflight_d22380a_2026-11-06.patch
python3 -m unittest -v tests.test_autokom_buildkit_preflight tests.test_autokom_groovy_sandbox
git diff --check
```

Après revue : `git add scripts/runtime/autokom_agent_local.py tests/test_autokom_buildkit_preflight.py docs/AUTOKOM-BUILDKIT-PREFLIGHT-2026-11-06.md` puis commit et push.

## Vérification AWS, sans modification

```bash
printf '%s\n' '== Binaries disponibles =='
for cmd in buildctl buildkitd rootlesskit; do command -v "$cmd" || true; done
printf '%s\n' '== Agent non privilégié =='
sudo -u autokom-builder sh -c 'id; command -v buildctl || true; command -v buildkitd || true'
printf '%s\n' '== Services BuildKit =='
systemctl list-units --type=service --all --no-pager '*buildkit*'
printf '%s\n' '== Configuration du service agent (sans secrets) =='
systemctl cat autokom-jenkins-agent.service | grep -E '^(User|Group|Environment=BUILDKIT_HOST|ExecStart)=' || true
printf '%s\n' '== Permissions du runtime =='
namei -l /var/lib/autokom/build-artifacts
printf '%s\n' '== Binaires apt =='
apt-cache policy buildkit moby-buildkit rootlesskit
```

Ne pas relancer le build tant que le client et le daemon rootless ne sont pas vérifiés.
Le prochain correctif de *provisionnement* doit être adapté au résultat de ces commandes : une source de binaires BuildKit approuvée, une unité systemd isolée, le socket privé, `BUILDKIT_HOST` défini dans l'unité de l'agent, et un test `buildctl debug workers` sous l'identité `autokom-builder`. Ne pas rejoindre le groupe Docker, ne pas donner accès au socket rootful, ne pas exposer de port public ni utiliser `--privileged`.
