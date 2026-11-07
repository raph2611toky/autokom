# AUTOKOM — Mot de passe du premier administrateur Jenkins (GCP)

## Problème observé

Dans `sudo autokom install`, après l'identifiant, le nom complet et l'e-mail,
la commande signalait de manière ambiguë :

> Mot de passe Jenkins invalide : entre 16 et 256 caractères, sans retour à la ligne

Elle recommençait alors tout le formulaire et non uniquement le mot de passe.
Une saisie masquée sans écho peut aussi donner l'impression que le terminal n'a rien reçu.

**La touche Entrée de validation n'est pas enregistrée dans le mot de passe par `getpass`**.
Si aucune touche n'a été saisie auparavant, le mot de passe est simplement vide.

## Correction

- Lecture par `getpass` sur le vrai terminal : pas d'écho, retour Entrée exclu.
- Aucun `.strip()` n'altère les caractères choisis par l'administrateur.
- Validation précise : vide, trop court (<16), trop long (>256), caractères interdits.
- Vérification de l'identité avant le mot de passe, puis en cas d'erreur seule la
  saisie secrète est reprise, **sans redemander l'identifiant, le nom ou l'e-mail**.
- En cas de confirmation différente, seuls le mot de passe et sa confirmation
  sont redemandés.
- Cinq essais de mot de passe au maximum, sans modifier Jenkins en cas d'échec.
- Aucune écriture de mot de passe dans les journaux ni les arguments de processus.
- Les valeurs enregistrées antérieurement (si présentes) sont réutilisées et
  les comptes Jenkins existants ne sont pas réinitialisés.

## Application

Sur Kali, à la racine du dépôt privé AUTOKOM :

```bash
unzip -o /home/raph/Downloads/AUTOKOM-JENKINS-MOT-DE-PASSE-TTY-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_jenkins_password_terminal_20261107 \
  tests.test_autokom_jenkins_manual_admin_20261107 \
  tests.test_autokom_jenkins_private_state_recovery_20261107

git diff --check
git add scripts/lib/jenkins_firstboot.py scripts/lib/jenkins_postinstall.py \
  tests/test_autokom_jenkins_manual_admin_20261107.py \
  tests/test_autokom_jenkins_password_terminal_20261107.py \
  docs/AUTOKOM-JENKINS-SAISIE-MOT-DE-PASSE-2026-11-07.md
git commit -m "fix: validate Jenkins admin password without resetting identity prompts"
git push origin main
```

Sur GCP :

```bash
sudo autokom update
sudo autokom install
```

Quand l'invite `Mot de passe Jenkins` apparaît, **taper au moins 16 caractères**, puis **Entrée**.
Même si les caractères ne s'affichent pas, ils sont normalement lus ; retaper le
**même** mot de passe à la confirmation et appuyer de nouveau sur **Entrée**.
Appuyer directement sur Entrée sans avoir saisi de caractères produit un mot
 de passe vide, ce qui est refusé normalement.

## Vérifications réalisées

- Un test sur un vrai pseudo-terminal Unix confirme que `getpass` reçoit le
  mot de passe complet suivi de Entrée, sans inclure le CR/LF et sans écho.
- Six tests nouveaux couvrent la validation, la conservation de l'identité,
  les confirmations et les espaces au début/à la fin.
- 65 tests ciblés passent.
- Suite générale : 891 tests contre 885 avant changement. Dans les deux cas,
  mêmes 11 anomalies préexistantes (6 échecs, 5 erreurs) ; aucune nouvelle
  régression détectée. Ces échecs ne sont pas présentés comme résolus.
- Jenkins/GCP distant n'a pas été exécuté par l'auteur du correctif.
