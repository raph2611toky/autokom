# AUTOKOM - OCI BuildKit: failure diagnostics after e9177ead65

## Observed evidence
`autokom runtime buildkit --apply` reported Ansible task 20 failed at the true OCI `RUN true` smoke test. All Django/broker/Jenkins checks were healthy. This only proves an OCI probe failed, **not its exact reason**. The original code loses the detailed `buildctl` stderr when producing the user-facing error.

## Changes
- `autokom_buildkit_admin.py --smoke-test` executes healthcheck and an uncached real OCI RUN using the already-managed private socket under the Jenkins builder identity.
- On failure, categorize the error; return code 2; print a bounded, non-secret administrative reason.
- Retain the fixed BusyBox probe's full last 128KiB in a root-only 0600 log under `/var/log/autokom/runtime/buildkit-smoke-*.log` (when the directory is safe); never expose raw trace to the customer API.
- Ansible intentionally captures the preflight result, then fails with the sanitized reason. A failed probe still blocks deployment. No privileged fallback, Docker socket, altered sysctl, or backend API changes.

## Deployment
Push the complete changed files to GitHub from Kali, then `sudo autokom update` and `sudo autokom runtime buildkit --apply` on Ubuntu. Read the summarized code or the private probe report to determine the exact next correction. Avoid blindly changing other systemd protections until the new error is classified.

## Preserve integration
Django -> UNIX broker -> Jenkins -> isolated agent -> private BuildKit socket stays unchanged. After the real smoke succeeds, run `sudo autokom runtime doctor` and only then a new customer deployment.

## Distribution ZIP à plat (6 novembre 2026)
Cette distribution contient directement les chemins `scripts/`, `ansible/`, `tests/`, `docs/`.
Depuis la racine du dépôt, après vérification de `git status --short` :
`unzip -o /home/raph/Downloads/AUTOKOM-BUILDKIT-DIAGNOSTIC-DIRECT-2026-11-06.zip`
Le ZIP remplace les fichiers complets, sans patch ni installateur.
La commande doit être exécutée uniquement dans le dépôt Git propre; `-o` écrase les fichiers énumérés.

La nouvelle catégorie `OCI_OS_ERROR` précise l'opération et le code errno lorsque le test
OCI échoue avant que `buildctl` ne renvoie une sortie. Aucune restriction systemd n'est assouplie.
Si le test révèle un refus, rechercher sa cause et corriger ensuite le rôle Ansible approprié.
