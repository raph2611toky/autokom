# AUTOKOM · DeepSAST et estimation de ressources

## Publier d’abord sur main

Sur le poste Windows, vérifier que le dépôt est propre avant d’extraire le ZIP.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git fetch origin
git switch main
git merge --ff-only origin/main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-DEEPSAST-RECAP-FIX.zip" -DestinationPath . -Force
git diff --check
git add -- scripts platform tests docs/AUTOKOM-DEEPSAST-RECAP-FIX.md
git diff --cached --stat
git commit -m "fix: restore DeepSAST runtime and show resource estimates"
git push origin main
```

Sur la VM principale, une fois la file des publications inactive, lancer ces
commandes. Le déploiement recharge les services de la plateforme. Il ne
redémarre ni les conteneurs clients ni les workers GCP.

```bash
sudo autokom update && sudo autokom devsecops apply --deploy && sudo autokom devsecops check
```

La commande installe Git LFS si nécessaire, récupère les poids vérifiés par
SHA-256 et installe l’inférence CPU dans `/opt/autokom-deepsast`. Les deux
environnements lisent ce même modèle. Le ZIP ne contient pas les poids. Le
premier téléchargement requiert de l’espace disque et des accès réseau vers les
dépôts système, GitHub et les index Python. L’installateur réutilise le compte
GitHub CLI nominatif configuré dans `/etc/autokom/update.env`, sans changer le
compte actif de `gh`.

## Préparer les captures

1. Dans le récapitulatif d’un projet, vérifier **Ressources recommandées**. La
   recommandation varie selon la configuration, les dépendances Node détectées et
   les répliques demandées. Elle ne modifie ni les quotas ni le provisionnement.
2. Publier un **nouveau** projet DevSecOps comportant du code source JavaScript,
   TypeScript ou Python. Ouvrir la progression et repérer **Analyse IA ·
   DeepSAST**. Dans **Rapport SAST**, capturer **Inférence locale terminée** et le
   nombre d’extraits analysés. Un rapport Semgrep sans résultat n’empêche pas le
   modèle d’inférer sur un échantillon du code source.
3. Comparer avec une nouvelle publication DevOps. Elle ne doit pas exécuter
   DeepSAST. Pour DevSecOps, le score du modèle reste consultatif et ne remplace
   pas le contrôle Semgrep.

Sur une publication groupée, consulter le rapport dans l’onglet du service
concerné. Ne pas tester l’envoi de rapport externe à partir d’un dépôt client.

## Réparer la préproduction ensuite

Sur Windows, après le test de `main`, avancer `master` si le merge est possible
en fast-forward. Si Git signale des branches divergentes, ne pas forcer le push.

```powershell
git fetch origin
git switch master
git merge --ff-only origin/master
git merge --ff-only main
git push origin master
```

Sur la VM principale, la commande de mise à jour de préproduction installe le
wrapper, déploie la console et teste le certificat en utilisant l’adresse
d’écoute Nginx plutôt que `127.0.0.1` imposé. Elle ne change pas les domaines
clients. Si les accès dédiés n’ont jamais été configurés, suivre l’indication
`sudo autokom-pre app setup` donnée par la CLI pour connecter le worker.

```bash
sudo autokom update preprod && sudo autokom-pre devsecops check
curl -fsS https://pre.auto.ikom.mg/api/health/
sudo autokom-pre app status
```
