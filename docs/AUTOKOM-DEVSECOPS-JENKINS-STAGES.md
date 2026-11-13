# AUTOKOM · Étapes Jenkins DevSecOps et correction npm

Ce correctif s’applique à main à partir de `84bf09a`. Il inclut également le
Jenkinsfile de préproduction, pour une mise à jour ultérieure de master.

## Diagnostic du build 44

Les deux tentatives de correction se terminaient avant npm. Sur le commit
`cc9f69f` de portfolio, le lockfile contient une plage TypeScript valide
de plus de 120 caractères. La validation AUTOKOM la rejetait. Elle accepte
maintenant les plages longues et les alias npm du registre, mais pas les
dépendances locales, les URL ou les registres non prévus.

L’audit npm réalisé sur une copie de ce lockfile a modifié le fichier mais
signalait encore des alertes. C’est la nouvelle analyse Trivy sur Jenkins,
pas le compteur npm, qui décidera si le build peut continuer. Si le nouveau
scan ne passe pas, vérifier les versions correctives du rapport. Ne pas forcer
une mise à jour majeure automatiquement. Une migration de la chaîne
`react-scripts` vers Vite peut être traitée dans le dépôt du portfolio.

## Déploiement

Sur Windows, vérifier d’abord que le dépôt est propre. Arrêter si une commande
Git échoue.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git switch main
git pull --ff-only origin main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-DEVSECOPS-JENKINS-STAGES.zip" -DestinationPath . -Force
git diff --check
git add -- docs/AUTOKOM-DEVSECOPS-JENKINS-STAGES.md platform scripts tests
git commit -m "fix: show DevSecOps stages in Jenkins and accept valid npm locks"
git push origin main
```

Sur la VM, attendre la fin des publications en cours. Déployer la plateforme
avant de synchroniser la définition du job, puis taper `SYNCHRONISER`
si la CLI le demande. Aucune édition du job dans l’interface Jenkins.

```bash
sudo autokom update &&
sudo autokom devsecops apply --deploy &&
sudo autokom runtime jenkins --apply &&
sudo autokom runtime doctor
```

Lancer une **nouvelle** publication depuis AUTOKOM. Les anciens builds Jenkins
gardent leurs étapes d’origine. En mode DevSecOps, la nouvelle vue montre
Semgrep, DeepSAST, le verdict SAST, Trivy, BuildKit et le contrôle dynamique
privé. En mode DevOps, les étapes de sécurité ne s’exécutent pas.

## Contrôle dynamique

Après un export réussi, Jenkins demande un contrôle au worker AUTOKOM.
Le broker démarre l’image dans un conteneur temporaire, sans port publié,
sans réseau externe et sans variables de production. Un second conteneur
AUTOKOM partage seulement sa boucle locale pour vérifier la réponse HTTP et
les en-têtes. La première exécution peut télécharger l’image officielle
`python:3.12-alpine`. Si l’application ne répond pas sans sa configuration
réelle, Jenkins indique un résultat non concluant. Le contrôle HTTP du
candidat réel demeure obligatoire avant activation, avec la vérification
HTTPS existante. Si Trivy bloque avant l’export, le contrôle dynamique ne
s’exécute pas.

Après validation de main, fusionner vers master par fast-forward si possible,
puis utiliser `sudo autokom update preprod` pour actualiser le job dédié.
