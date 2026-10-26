# Jenkins — Quick onboarding v2C

- Existing Jenkins: display verified URL `https://<domain>/me/security/`, ask Jenkins login and API token with hidden prompt, then install plugin profile.
- New Jenkins: guide official first-run wizard and wait for **completion marker**, not merely `config.xml` and `admin` user. After wizard, same token onboarding resumes automatically.
- Token only in memory and ephemeral private file `/run/autokom-jenkins-*/auth`; deleted after safe-restart or failure.
- One menu for recommended profile; custom path keeps individual choices.
- No password reset, no unauthenticated Groovy console, no disabling Jenkins security.

## Scan Multibranch par webhook (choix explicite)

Le plugin Jenkins s’appelle exactement **`multibranch-scan-webhook-trigger`** (version publiée 1.0.11). Il est désormais visible dans le menu interactif, après le choix du profil : **1 = ne pas installer** (défaut recommandé ; les événements GitHub Branch Source suffisent pour les dépôts GitHub), **2 = ajouter le plugin**. Les autres plugins du profil restent installés comme avant.

Pour activer explicitement via CLI :

```bash
sudo bash scripts/install/07-jenkins-postinstall.sh --check --profile devsecops --multibranch-scan
```

Puis seulement après inspection et sauvegarde Jenkins, `--apply --profile devsecops --multibranch-scan --confirm-restart` avec authentification admin. L’option interactive `--apply --interactive` pose la question et ne nécessite pas `--multibranch-scan`. Aucun token webhook ne doit être exposé dans les URLs de journaux ; préférer l’en-tête HTTP `token` et un secret robuste.

**Attention :** le catalogue Jenkins signale un avertissement de sécurité non résolu **SECURITY-2875 / CVE-2023-46656** (comparaison de token non constante). Ce plugin demeure **hors profil installé automatiquement par défaut**. Le plugin de base `github-branch-source` déclenche les scans multibranch pour GitHub sans devoir ajouter ce plugin.
