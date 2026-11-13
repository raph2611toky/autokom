# AUTOKOM · Correctifs de dépendances, alertes et restauration

Base de ce correctif : commit `8665d5c`, présent sur main et master. Le ZIP ne
contient que les fichiers du correctif, aux chemins relatifs du dépôt AUTOKOM.

## Mise en service

Sur Windows, vérifier que le dépôt ne contient aucune modification locale, puis
arrêter si une commande Git échoue.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git fetch origin
git switch main
git merge --ff-only origin/main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-SECURITE-CORRECTIFS-ROLLBACK.zip" -DestinationPath . -Force
git diff --check
git add -- docs/AUTOKOM-SECURITE-CORRECTIFS-ROLLBACK.md platform scripts tests
git diff --cached --stat
git commit -m "feat: remediate npm findings and restore published releases"
git push origin main
```

Sur Linux, l’extraction équivalente, à la racine du dépôt de travail :

```bash
unzip -o AUTOKOM-SECURITE-CORRECTIFS-ROLLBACK.zip -d /home/raph/SOUTENANCE/AUTOKOM
```

Sur la VM, attendre la fin des publications. La commande applique la migration
`deployments.0006` et recharge la plateforme. Aucun push n’est effectué vers un
dépôt client. Omettre `--auto-notify` si les notifications externes doivent rester
manuelles. Ce paramètre est persistant une fois activé. Pour le désactiver,
remettre `AUTOKOM_SECURITY_AUTO_NOTIFY=false` dans la configuration backend et
redéployer.

```bash
sudo autokom update
sudo autokom devsecops apply --deploy --auto-notify
sudo autokom devsecops check
```

GitHub doit autoriser l’App AUTOKOM à écrire des issues dans le dépôt du projet.
Les issues privées contiennent un résumé borné et des suggestions. Dans un dépôt
public, seule une invitation à ouvrir le rapport privé AUTOKOM est publiée.
L’e-mail est adressé au créateur autorisé du projet, dont l’adresse doit être
vérifiée et présente dans `AUTOKOM_SECURITY_EMAIL_RECIPIENTS`. SMTP doit être
configuré. La notification d’un résultat n’est jamais un critère de publication.
Les rapports restent disponibles dans l’interface indépendamment de ces canaux.

## Valider

1. Publier un projet DevSecOps avec un profil npm géré. L’étape SCA tente un
   correctif compatible sans `--force` ni hooks npm. Le rapport affiche les CVE
   détectées, les versions correctives annoncées et, si le scan repasse, les
   versions trouvées dans le lockfile de build. Vérifier le badge **Correctifs
   appliqués · à reporter dans GitHub** et vérifier que le commit source
   n’a pas changé. Un correctif est appliqué à la copie Jenkins uniquement.
   Le build vérifie l’empreinte du lockfile analysé avant de le consommer.
2. Si le contrôle détecte encore des alertes élevées/critiques, un secret ou
   un blocage Semgrep, la publication ne passe pas. DeepSAST reste consultatif,
   et les suggestions Semgrep proviennent des règles AUTOKOM. Les classes du
   modèle ne sont pas présentées comme des CVE ni utilisées pour réécrire le code.
   Un Dockerfile personnalisé, un workspace npm ou un registre non standard ne
   bénéficie pas de la correction automatique. Le rapport reste disponible.
3. Publier deux versions sûres d’un projet, puis ouvrir **Historique** et
   **Restaurer cette version** sur la première. Vérifier que la version active
   change seulement après les contrôles HTTP/HTTPS. La restauration réutilise
   l’image historique ou son archive locale, sans relancer Git ou Jenkins.
   Pour une publication frontend + backend, les deux services sont restaurés
   ensemble. Les variables et secrets actuels sont utilisés. La base de
   données n’est pas restaurée. Une ancienne version DevOps ne peut pas
   contourner une configuration actuelle DevSecOps. Un artefact introuvable,
   un domaine groupé changé ou un état de publication incertain font échouer
   la restauration sans la déclarer réussie.
4. En production, l’image restaurée est ensuite éligible à la promotion GCP
   existante. Le test local HTTPS ne vaut pas confirmation que le load
   balancer public ou ses répliques ont basculé.

## Préproduction

Après validation en production, avancer master par fast-forward et utiliser
`sudo autokom update preprod`. Les notifications externes restent désactivées
en préprod. Exécuter une vraie publication de démonstration pour confirmer
Jenkins, BuildKit, Trivy, MySQL, la livraison des rapports et les accès réseau.
