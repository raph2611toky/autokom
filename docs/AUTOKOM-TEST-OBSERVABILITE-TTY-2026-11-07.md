# Correctif de test — Observabilité TTY / sans TTY (7 novembre 2026)

## Symptôme

Dans un terminal Kali interactif, la commande de test de la correction Certbot échouait :

```
FAIL: test_disable_live_without_tty
AssertionError: 0 != 1
```

## Cause

`observability.run()` utilise intentionnellement `subprocess.Popen()` lorsqu'une sortie TTY est disponible et que le loader est activé, et `subprocess.run()` dans le cas contraire. Le test supposait implicitement que stdout n'était jamais un TTY. Il échouait donc lorsque le test était exécuté dans le terminal interactif de Kali, sans que l'application soit défaillante.

## Correction

Le test force explicitement `sys.stdout.isatty()` à faux, puis vérifie `subprocess.run()`. Deux autres tests couvrent le cas TTY interactif et le mode `AUTOKOM_LIVE=0`, dans lequel le loader doit rester désactivé même avec un TTY.

**Aucun fichier runtime, mot de passe, service, DNS ou configuration ne change.**

## Vérification

Depuis la racine du dépôt :

```bash
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_acme_webroot_404_20261107 \
  tests.test_autokom_acme_retry_gcp_20261107 \
  tests.test_optional_letsencrypt_email_v15 \
  tests.test_observability \
  tests.test_observability_tools_v16
```

Les 66 tests ciblés sont passés en mode sans TTY **et** en mode TTY simulé avec `script`. Ces tests ne prouvent pas que la délivrance ACME sur GCP a déjà réussi.
