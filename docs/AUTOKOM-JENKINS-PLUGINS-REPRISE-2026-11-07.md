# AUTOKOM — installation Jenkins DevSecOps reprenable (GCP)

Correctif différentiel du 7 novembre 2026. Pas de réinitialisation Jenkins,
pas de changement de mots de passe ou de jeton, aucun changement DNS/MySQL/HTTPS.

## Échec observé

Sur Jenkins LTS 2.580.2, la création de l'administrateur choisi dans AUTOKOM
(`AdminJenkins`) et du jeton technique a réussi. L'appel groupé
`jenkins-cli install-plugin PLUGIN1 PLUGIN2 ...` s'est ensuite arrêté avec
`Failed to install plugin pipeline-stage-view` (code 5). Le journal fourni
ne permet pas d'identifier avec certitude le défaut réseau, miroir ou
dépendance. Stage View 2.41 requiert Jenkins 2.479.1, donc cette version est
compatible avec 2.580.2.

## Changement

- Installations **une par une**, sans écraser/supprimer les plugins présents.
- Refus de considérer une archive `.jpi/.hpi` tronquée comme un plugin présent ;
  aucun effacement automatique de fichiers suspects.
- Actualisation **signée** du catalogue Jenkins une fois seulement en cas
  de problème ; deuxième essai après la mise à jour.
- Installation explicite de `pipeline-rest-api` avant Stage View : dépendance
  requise du plugin Stage View.
- En dernier recours **pour Stage View uniquement**, récupération de
  `pipeline-stage-view 2.41` auprès des distributions officielles Jenkins,
  validation SHA-256 exacte
  `fbc9dd724278f69b8c7638907363a609a8484cfaaa59321169468ee6c908952f`,
  contrôle du manifeste, puis import via Jenkins CLI. Le contrôleur doit
  annoncer Jenkins >=2.479.1 pour autoriser cette reprise.
- Le profil se poursuit pour les autres plugins si un téléchargement échoue,
  mais **le module retourne un échec** tant qu'un plugin sélectionné manque.
  Aucun succès ni redémarrage n'est prétendu en cas d'échec partiel.
- Si une restriction Matrix AUTOKOM attend son application, `matrix-auth`
  est traité avant les plugins d'interface.
- Journal technique non secret sous
  `/var/log/autokom/jenkins/plugins-YYYYMMDDTHHMMSSZ-*.jsonl` (root:0600,
  répertoire root:0700). Identifiants de plugins, état et catégorie d'erreur,
  sans mot de passe, jeton, URL interne ni sortie brute du serveur.

## Mise en œuvre

Sur Kali (dans le dépôt privé) :

```bash
unzip -o /home/raph/Downloads/AUTOKOM-JENKINS-PLUGINS-RESILIENTS-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_jenkins_plugin_recovery_20261107 \
  tests.test_jenkins_postinstall tests.test_jenkins_onboarding

git diff --check
git add scripts/lib/jenkins_plugin_recovery.py \
  scripts/lib/jenkins_postinstall.py \
  config/jenkins/plugins-baseline.txt \
  tests/test_autokom_jenkins_plugin_recovery_20261107.py \
  docs/AUTOKOM-JENKINS-PLUGINS-REPRISE-2026-11-07.md
git commit -m 'fix: resume Jenkins plugin installs with verified Stage View fallback'
git push origin main
```

Sur GCP :

```bash
sudo autokom update
sudo autokom install
sudo autokom status
sudo systemctl is-active jenkins
```

Ne pas relancer l'assistant Web Jenkins et ne pas supprimer
`/var/lib/jenkins`, `/etc/autokom/secrets/jenkins-admin.auth` ni les comptes.

## Si le téléchargement échoue encore

Lire les derniers événements root-privés, **sans publier de secrets** :

```bash
sudo ls -lt /var/log/autokom/jenkins/plugins-*.jsonl | head
sudo journalctl -u jenkins -n 160 --no-pager \
  | grep -Ei 'plugin|download|dependency|exception|error|SSL|timeout' \
  | tail -55
```

Classer l'échec : code Jenkins `install-plugin` ≠ cause exacte du refus.
Les échecs issus de l'environnement distant (réseau, DNS, proxy, miroir)
peuvent persister malgré cette reprise. Les tests locaux n'exécutent pas
Jenkins sur le serveur GCP.

## Vérifications locales

- Tests du chemin primaire, du rafraîchissement du catalogue, de l'échec
  partiel, de la reprise et du contrôle d'intégrité des archives.
- Pas de modification de compte, mot de passe ou jeton.
- `--check` reste une simulation sans téléchargement.
