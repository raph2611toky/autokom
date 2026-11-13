# AUTOKOM préproduction · branche master

> Ce guide décrit le premier patch. Pour les commandes automatisées, utiliser
> [AUTOKOM-DEVSECOPS-PREPROD-AUTO.md](AUTOKOM-DEVSECOPS-PREPROD-AUTO.md).

Ce patch part de `172c70c`. Le serveur et le checkout de production restent sur
`main`. Ne pas lancer `autokom app deploy`, `install.sh`, Terraform ou le
contrôleur HA depuis le checkout de préproduction.

## Ce qui est séparé

| Élément | Préproduction |
|---|---|
| Console | `https://pre.auto.ikom.mg`, Nginx de la VM principale |
| Source | `/opt/autokom-preprod/source`, branche `master` |
| MySQL | Même serveur, base et utilisateur `autokom_preprod` |
| Services | `autokom-preprod-web`, `autokom-preprod-broker`, `autokom-preprod-worker` |
| Port et socket | `127.0.0.1:8272`, `/run/autokom-preprod/platform-control.sock` |
| Configuration et état | `/etc/autokom-preprod`, `/var/lib/autokom-preprod` |
| Jenkins | Même contrôleur et agent BuildKit isolé, job `autokom-preprod-client-deploy`, credential `autokom-preprod-runner-key`, compte limité à ce job |
| Projets | L'environnement Production utilise le domaine géré normal. L'environnement Préproduction réserve `pre.<domaine-géré-de-production>`. |

Un nom de projet est indépendant de la branche de la console. Un domaine ne
peut pas être desservi par deux environnements en même temps. Les domaines
clients de la console préprod doivent être attribués au préalable dans
`AUTOKOM_PREPROD_ALLOWED_HOSTS`, et leur DNS et leur vhost ne doivent pas
appartenir à la production. Pour un projet de soutenance, utiliser un slug
dédié, par exemple `demo-soutenance`. Les domaines personnalisés ne sont pas
pris en charge par cette console. Pour un déploiement groupé, le formulaire
stack actuel reste lié à ses environnements Production et Backend Production.
Le patch ne crée pas un deuxième stack par environnement.

Le MIG, son autoscaler et ses files de livraison ne sont pas modifiés.
La console de préproduction n'annonce pas de HA et ne publie pas dans la
file de livraison du MIG de production. Pour tester cette fonctionnalité,
suivre le test de bascule existant sur une application GCP répliquée. Pour
activer la HA depuis `master`, il reste à raccorder un état de delivery
et des règles d'ingress isolés.

## 1. Publier master depuis Windows PowerShell

Depuis le dépôt AUTOKOM, placer le ZIP dans Téléchargements. Ne pas poursuivre
si `git status --short` montre des changements à conserver, ou si
`git ls-remote --heads origin master` renvoie déjà une branche.

```powershell
Set-Location 'F:\SOUTENANCE\TOKY\autokom'
git status --short
git fetch origin main
git ls-remote --heads origin master
git switch -c master origin/main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-MASTER-PREPROD-DEEPSAST.zip" -DestinationPath (Get-Location).Path -Force
git diff --check
git status --short
git add -- autokom scripts platform tests docs/AUTOKOM-PREPROD-DEEP-SAST.md
git diff --cached --stat
git commit -m "feat: isolate AUTOKOM preprod and integrate local DeepSAST"
git push -u origin master
git ls-remote --heads origin main master
```

Ne pas ajouter `models/` de nouveau. Le commit de départ pointe déjà vers
l'objet LFS de 437 958 648 octets. Le ZIP ne contient ni poids ni secrets.

## 2. Préparer la console sur la VM principale

Utiliser le compte d'administration de la VM, avec Git, Git LFS, Python venv,
les en-têtes mysqlclient, Node >= 20.19, Nginx, MySQL, Certbot et son plugin
DNS Cloudflare. Conserver le compte GitHub déjà configuré. Le dépôt était
lisible sans authentification lors de la préparation du patch.

```bash
sudo install -d -o root -g root -m 0755 /opt/autokom-preprod
sudo env GIT_LFS_SKIP_SMUDGE=1 git clone --single-branch --branch master \
  https://github.com/raph2611toky/autokom.git /opt/autokom-preprod/source
cd /opt/autokom-preprod/source
sudo bash scripts/preprod/manage.sh check
sudo bash scripts/preprod/manage.sh init
```

`init` lit uniquement la configuration publique d'ingress et les paramètres
Cloudflare nécessaires depuis les fichiers protégés de la VM. Il crée ses
propres fichiers, une base vide et des clés distinctes. Il ne copie ni données
ni utilisateurs de production. Remplacer la copie du jeton Cloudflare par un
jeton de la zone `ikom.mg` dédié si disponible. Ne pas afficher le contenu
des fichiers privés dans une capture.

Configurer une GitHub App de préproduction ou des callbacks supplémentaires
sur l'App existante pour OAuth, sans changer ceux de production. Pour les
webhooks, une App séparée est requise pour conserver le webhook de production.
Le dépôt doit être installé et accessible au compte test.

Dans `sudoedit /etc/autokom-preprod/backend.env`, ajouter les valeurs
appropriées, en remplaçant les exemples. Ne pas écraser les clés générées.

```dotenv
GITHUB_APP_CLIENT_ID=...
GITHUB_APP_CLIENT_SECRET=...
GITHUB_APP_SLUG=...
GITHUB_APP_CALLBACK_URL=https://pre.auto.ikom.mg/api/projects/git/github/callback/
GITHUB_AUTH_CALLBACK_URL=https://pre.auto.ikom.mg/auth/github/callback
GITHUB_WEBHOOK_SECRET=...
```

URL webhook de l'App dédiée (événement push, facultatif) :
`https://pre.auto.ikom.mg/api/projects/git/github/webhook/`.
Pour ouvrir une issue, l'App et le compte doivent pouvoir écrire dans Issues.
Pour commenter une PR, prévoir Pull requests en lecture et les droits
d'écriture appropriés sur Issues ou Pull requests. Le commentaire exige une
PR du même dépôt dont la tête est exactement le commit analysé. Aucun correctif
automatique, push de code, ouverture de PR ou fusion n'est effectué.

Pour envoyer des notifications de soutenance, utiliser le relais SMTP de
préproduction et activer explicitement l'envoi. Le résumé par e-mail n'est
adressé qu'au compte demandeur vérifié et autorisé dans la liste ci-dessous.
Les autres e-mails Django (par exemple vérification de compte) utilisent le
backend SMTP configuré par ces réglages. Sans SMTP, utiliser la connexion
GitHub avec une adresse vérifiée pour les comptes test.

```dotenv
AUTOKOM_SECURITY_FEEDBACK_ENABLED=true
AUTOKOM_PREPROD_EMAIL_ENABLED=true
AUTOKOM_SECURITY_EMAIL_RECIPIENTS=adresse-verifiee@example.org
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=...
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=...
```

Ne pas ajouter deux fois EMAIL_BACKEND. On peut activer les actions GitHub
sans activer SMTP.

Puis construire, migrer et publier uniquement la console de préproduction :

```bash
sudo bash scripts/preprod/manage.sh deploy
curl -fsS https://pre.auto.ikom.mg/api/health/
sudo systemctl status autokom-preprod-web.service autokom-preprod-broker.service --no-pager
```

Ce déploiement crée si nécessaire un A **DNS only** pour
`pre.auto.ikom.mg` vers l'ingress de la VM principale. Il refuse un
enregistrement conflictuel, demande un certificat DNS‑01 propre au nom,
teste Nginx et recharge sa configuration sans redémarrer la production.
Il ne modifie pas la zone ni la configuration du load balancer GCP.
Vérifier les deux consoles après la publication.

## 3. Brancher Jenkins et le modèle

```bash
sudo git -C /opt/autokom-preprod/source lfs install --local
sudo git -C /opt/autokom-preprod/source lfs pull --include='models/deep_sast_model_3_Hbol/model.safetensors' --exclude=''
sudo bash scripts/preprod/manage.sh model
sudo python3 scripts/preprod/jenkins-job.py
sudo python3 scripts/preprod/jenkins-job.py --apply
```

Le script Jenkins demande un compte administrateur et un jeton API de façon
interactive pour la création du job dédié. Il ne les enregistre pas. Il
n'écrase pas un job existant dont le contenu diffère. S'il a déjà été créé
avec une version différente, comparer son pipeline au Jenkinsfile du patch
avant d'actualiser **ce job uniquement** dans Jenkins.

Dans Jenkins, créer un credential Secret text `autokom-preprod-runner-key`
pour ce pipeline, égal à `AUTOKOM_RUNNER_SHARED_KEY` du backend de préprod.
Réserver l'administration des jobs aux opérateurs. Créer un compte de service
et un jeton dédiés, limiter les droits au job de préproduction (lire, lancer
et annuler) et à la lecture de l'état de l'agent, puis renseigner
`sudoedit /etc/autokom-preprod/runtime.env` :

```dotenv
AUTOKOM_JENKINS_USER=...
AUTOKOM_JENKINS_API_TOKEN=...
AUTOKOM_PREPROD_ALLOWED_HOSTS=demo-soutenance.auto.ikom.mg,pre.demo-soutenance.auto.ikom.mg,backend.demo-soutenance.auto.ikom.mg
```

Retirer de cette liste tout domaine déjà utilisé par la production. Le
broker refuse les destinations DNS non autorisées et les enregistrements
existants qui ne lui appartiennent pas. La liste n'est pas accessible au client.

```bash
sudo bash scripts/preprod/manage.sh enable-worker
sudo journalctl -u autokom-preprod-worker.service -n 50 --no-pager
```

## 4. Démonstration et mises à jour

1. Dans la console préprod, publier un dépôt de démonstration en DevOps.
   Vérifier que les scanners ne sont pas exécutés et que l'URL répond.
2. Sur le même dépôt, choisir Préproduction et DevSecOps. Vérifier l'URL
   `pre.demo-soutenance.auto.ikom.mg`, la lecture Semgrep/Trivy et le
   rapport DeepSAST. Les résultats IA sont consultatifs. Semgrep garde son
   comportement bloquant. Le modèle ne lit que des extraits locaux, issus
   d'au plus 24 localisations remontées par Semgrep. Aucune requête de
   classification ne part vers un service externe.
3. Avec une règle Semgrep bénigne et contrôlée dans un dépôt de démonstration,
   vérifier l'état bloqué et le panneau Rapport SAST. Tester une issue, un
   commentaire sur une PR correspondante et l'e-mail seulement après activation
   des canaux. Aucun envoi n'est déclenché automatiquement à partir d'un scan.
4. Vérifier qu'un nouvel essai du même canal pour le même run ne crée pas de
   doublon. Si l'issue ou l'e-mail n'est pas confirmé, vérifier la destination
   avant de réessayer. Le modèle ne fournit pas de niveau de sévérité ni de score
   de sécurité. Aucun scan DAST offensif automatisé n'est ajouté.

Pour une nouvelle version, laisser les exécutions de préprod se terminer,
puis arrêter **son** worker avant la mise à jour et le redéploiement :

```bash
cd /opt/autokom-preprod/source
sudo ./autokom update --preprod --check
sudo systemctl stop autokom-preprod-worker.service
sudo ./autokom update --preprod
sudo bash scripts/preprod/manage.sh deploy
sudo bash scripts/preprod/manage.sh enable-worker
```

`update --preprod` n'accepte que le checkout fixé sur `master`, utilise
la logique existante de lecture GitHub, n'effectue qu'un fast-forward,
ne télécharge pas à nouveau les poids et n'appelle pas les hooks HA.
Continuer d'utiliser la commande habituelle de production pour `main`.

Pour couper la préproduction, arrêter son worker, puis son web et son broker.
La base, les conteneurs clients et le DNS sont conservés. Retirer les sites
clients via le flux de suppression de la console avant de couper son broker
si l'objectif est de les dépublier aussi. Ne supprimer aucune ressource de
production.
