> **Utilisation actuelle :** `sudo autokom install` (automatique), `autokom status`, `sudo autokom check`. Les commandes internes ci-dessous restent destinées à l’expertise.

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
sudo bash scripts/install/jenkins.sh --check --profile devsecops --multibranch-scan
```

Puis seulement après inspection et sauvegarde Jenkins, `--apply --profile devsecops --multibranch-scan --confirm-restart` avec authentification admin. L’option interactive `--apply --interactive` pose la question et ne nécessite pas `--multibranch-scan`. Aucun token webhook ne doit être exposé dans les URLs de journaux ; préférer l’en-tête HTTP `token` et un secret robuste.

**Attention :** le catalogue Jenkins signale un avertissement de sécurité non résolu **SECURITY-2875 / CVE-2023-46656** (comparaison de token non constante). Ce plugin demeure **hors profil installé automatiquement par défaut**. Le plugin de base `github-branch-source` déclenche les scans multibranch pour GitHub sans devoir ajouter ce plugin.

## Fiabilité et lisibilité du parcours interactif (v7)

Depuis `sudo autokom install`, une instance Jenkins existante suit une seule
séquence claire : profil des plugins → **compte administrateur** → application.
Un cadre Jenkins compact apparaît (sans grande bannière, réservée aux premières
installations). Les messages indiquent les plugins déjà présents et manquants ;
les questions ne sont affichées que si un choix est nécessaire.

Pour l'authentification, AUTOKOM propose le lien HTTPS de création du token,
puis demande **l'identifiant de connexion Jenkins** et le **token masqué**.
L'authentification est contrôlée côté Jenkins avec `who-am-i`, puis les droits
`Overall/Administer` avec une commande Groovy en lecture seule. Le compte et
les droits doivent être valides **avant la moindre installation de plugin ou
modification de sécurité**.

- Trois essais maximum en mode interactif en cas de mauvais compte, de token
  vide, de token incorrect ou de droits insuffisants. Le nombre d'essais
  restants est indiqué. La confirmation d'un mot de passe pour un nouvel
  administrateur autorise également trois essais.
- Aucun nouvel essai automatique sur une panne réseau ou système. `Ctrl+C`
  annule. L'échec du troisième essai interrompt uniquement la phase en cours :
  une relance de `sudo autokom install` reprend les composants non conformes.
- Les tokens ne figurent jamais dans la ligne de commande, les messages d'erreur
  publics ou les journaux. Les fichiers temporaires `0600` dans `/run` sont
  détruits après chaque essai et après l'installation.
- Un mode sans interaction conserve `--auth-file` (`root`, permission `0600`).
  Il n'effectue **pas** trois essais silencieux avec les mêmes mauvais secrets.
- L'animation de contrôle tourne uniquement **après** la saisie et s'efface à
  la fin. Elle ne perturbe jamais une question active.

Test de non-régression avant AWS :

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
sudo autokom install --check
```

L'exécution `--check` ne demande jamais de token et n'installe aucun plugin.
