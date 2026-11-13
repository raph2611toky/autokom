# AUTOKOM — DevSecOps sur main, préproduction par la CLI

Ce correctif complète le commit master `9c2b50d` et suppose que main est
toujours à `172c70c`. Ne pas écraser une branche qui a avancé.
Le ZIP contient le delta complet par rapport à main, aucun poids et aucun secret.
Pour le déploiement de ce correctif, ce guide remplace les commandes de l'ancien
guide AUTOKOM-PREPROD-DEEP-SAST.

## 1. Publier le code depuis PowerShell

Vérifier que le dépôt est propre et que les références correspondent avant de
continuer. Aucune opération de force-push n'est nécessaire.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git fetch origin
git status --short
git log -1 --oneline origin/main
git log -1 --oneline origin/master
git switch master
git merge --ff-only origin/master
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-DEVSECOPS-PREPROD-AUTO.zip" -DestinationPath (Get-Location).Path -Force
git diff --check
git add -- .gitattributes autokom scripts platform tests docs
git update-index --chmod=+x autokom scripts/preprod/manage.sh
git diff --cached --stat
git commit -m "feat: automate AUTOKOM preprod and enable DevSecOps on main"
git push origin master
git switch main
git merge --ff-only origin/main
git merge --ff-only master
git push origin main
```

Ne pas ajouter de nouveau models/. Ne pas modifier Terraform, la taille du MIG,
le load balancer ou le DNS des applications pour cette livraison.

## 2. Activer DevSecOps sur main

Sur la VM principale, attendre la fin des publications clients, puis lancer :

```bash
sudo autokom update
sudo autokom devsecops apply --deploy
sudo autokom devsecops check
sudo autokom runtime doctor
```

La commande installe le modèle et les dépendances d'inférence CPU dans
`/opt/autokom-deepsast`, vérifie les poids et fait une inférence de contrôle sous
`autokom-builder`. Elle configure les rapports, applique Django et React,
puis vérifie les unités. L'activation redémarre Gunicorn et le plan de contrôle
de production. Elle ne redémarre ni les conteneurs clients ni les workers GCP.
Si la vérification de la file ne confirme pas qu'elle est inactive, la
publication du code de production est interrompue avant le redémarrage.

Les deux pipelines utilisent le même modèle de 437 958 648 octets.
Une fois le modèle installé, la commande suivante le vérifie sans le télécharger
de nouveau. La première installation a besoin de Git LFS, de Python venv,
d'espace disque pour PyTorch CPU et d'accès à GitHub et aux index Python.
Le modèle est chargé en local avec `local_files_only=True` et
`trust_remote_code=False`. Semgrep reste la décision bloquante sur le code,
le résultat DeepSAST reste consultatif. DevOps ignore Semgrep, Trivy, DeepSAST
et le contrôle dynamique.

Les nouveaux runs DevSecOps incluent un contrôle dynamique défensif **sur
l'endpoint de santé du candidat privé**. Le broker vérifie que le candidat
appartient à ce run, puis envoie un GET sur son relais loopback, sans proxy
ni redirection. Il contrôle le code HTTP et, lorsque la réponse est HTML,
la présence de protections CSP, nosniff et iframe. Les protections absentes
sont des avertissements, une réponse HTTP invalide bloque le candidat avant
publication. Il n'y a pas de crawl, d'injection ni de scan Nikto automatisé.

Le panneau Rapport SAST permet d'ouvrir une issue ou de commenter une PR
correspondant au commit. L'action est explicite et ne diffuse pas de code.
L'App GitHub doit être autorisée sur le dépôt avec les permissions adéquates.
L'envoi par e-mail nécessite SMTP et `AUTOKOM_SECURITY_EMAIL_RECIPIENTS`
dans la configuration backend. Aucune issue ni aucun e-mail ne sont envoyés
automatiquement à partir d'un scan.

## 3. Installer ou réparer la préproduction

Après publication de master et mise à jour de main sur la VM :

```bash
sudo autokom update preprod --check
sudo autokom update preprod
sudo autokom-pre app status
curl -fsS https://pre.auto.ikom.mg/api/health/
```

La CLI récupère master, vérifie la base dédiée, répare la copie de backend.env
de production identifiée, construit React, applique Django, installe les
services de préproduction et publie le DNS Cloudflare et le certificat TLS
de la console. La sauvegarde de l'ancienne copie reste root:0600 et la
production n'est pas migrée par cette commande. Ne pas recopier backend.env.

Si le worker n'est pas encore connecté, la console peut fonctionner seule.
Exécuter une fois :

```bash
sudo autokom-pre app setup
```

L'assistant reçoit les identifiants d'une GitHub App autorisée et d'un compte
de service Jenkins dédié, ainsi qu'une liste de domaines de démonstration
inoccupés. Exemple : `demo-soutenance.auto.ikom.mg,pre.demo-soutenance.auto.ikom.mg`.
Un compte de provisioning Jenkins avec les droits de créer le job et son
credential est nécessaire. L'assistant accepte des identifiants distincts
pour cela. Il ne crée pas les comptes Jenkins, ne modifie pas les permissions
et ne réutilise pas les identifiants du compte de production comme worker.

Enregistrer côté GitHub ces callbacks sur l'App utilisée par la préproduction :

- `https://pre.auto.ikom.mg/auth/github/callback`
- `https://pre.auto.ikom.mg/api/projects/git/github/callback/`

Si une GitHub App de préproduction doit recevoir des webhooks, régler son URL
sur `https://pre.auto.ikom.mg/api/projects/git/github/webhook/` avec un secret
dédié. Le webhook n'est pas requis pour les lancements manuels. Le provisioning
GitHub et les permissions du compte Jenkins restent des prérequis, pas des
opérations que cette CLI peut effectuer sans autorisation externe.

Après cette connexion initiale, `sudo autokom-pre update` ou
`sudo autokom update preprod` effectue la mise à jour suivante. `autokom-pre`
prend en charge update, app setup/deploy/status/logs, app frontend deploy,
runtime doctor/status/logs et devsecops check. Les commandes qui administrent
la machine entière, notamment Terraform et le MIG, restent sur autokom.
Aucune HA publique de préproduction n'est déclarée.

## 4. Vérification

1. Lancer un projet de démonstration en DevOps. Aucun scanner ni étape
   dynamique ne doit apparaître.
2. Lancer le même dépôt en DevSecOps. Sur un run qui atteint un candidat sain,
   vérifier le passage du contrôle dynamique avant l'activation publique.
3. Sur un commit de démonstration signalé par une règle Semgrep approuvée,
   vérifier le résumé DeepSAST et l'issue GitHub à la demande. Le blocage
   Semgrep n'active pas le candidat et l'étape dynamique est alors ignorée.
4. Sur la console préprod, vérifier le nom de domaine de l'environnement
   Préproduction et les journaux avec `sudo autokom-pre app logs`.
