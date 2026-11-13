# AUTOKOM · Correctifs npm ciblés et notifications

Base du ZIP AUTOKOM : `8faeae1` sur main. Extraire dans le dépôt AUTOKOM,
puis envoyer le commit sur main. Ne pas extraire le correctif portfolio dans
ce même dépôt.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git switch main
git pull --ff-only origin main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-CORRECTIFS-CIBLES-NOTIFICATIONS.zip" -DestinationPath . -Force
git diff --check
git add -- docs/AUTOKOM-CORRECTIFS-CIBLES-NOTIFICATIONS.md platform scripts tests
git commit -m "fix: apply targeted npm remedies and clarify security reports"
git push origin main
```

Une fois les publications en cours terminées, sur la VM :

```bash
sudo autokom update &&
sudo autokom devsecops apply --deploy --auto-notify &&
sudo autokom devsecops check
```

Le Jenkinsfile ne change pas. L’e-mail nécessite un compte vérifié autorisé dans
`AUTOKOM_SECURITY_EMAIL_RECIPIENTS` et un SMTP configuré. Une issue publique
contient le verdict, des commandes génériques et le lien du rapport authentifié.
L’e-mail, l’issue privée et l’interface AUTOKOM contiennent les commandes
adaptées au dépôt et les versions correctives. Un SAST avec des résultats ou
un échec du scanner peut envoyer un rapport même sans résultat Trivy.

## Validation

Sur un projet npm géré, si les deux tentatives génériques ne suffisent pas,
AUTOKOM applique une troisième tentative à partir des versions correctives
signalées par Trivy. Elle cible des dépendances transitives et ne force
ni changement de branche majeure, ni modification des dépendances directes,
ni écrasement des overrides existants. Le manifeste et le lockfile restent
dans la copie Jenkins. Tous deux sont vérifiés avant la compilation. Trivy
doit repasser avant la publication. Le dépôt client n’est pas modifié.

Dans le rapport, ouvrir **Commandes pour corriger dans le dépôt** et copier
le bloc. Les commandes de reproduction exactes ne sont affichées que pour
les overrides validés par le nouveau scan. Relire les résultats avant de
pousser. Une correction majeure ou l’absence de correctif nécessite une
modification du projet. Le blocage DevSecOps est conservé.
