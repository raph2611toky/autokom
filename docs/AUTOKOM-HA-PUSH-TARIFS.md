# AUTOKOM · HTTPS HA, push GitHub et tarifs

Le ZIP contient les fichiers modifiés, à extraire à la racine d’AUTOKOM. Base vérifiée `4b1dc0c`. Ne pas extraire le correctif Vite dans le dépôt AUTOKOM.

```powershell
git switch main
git pull --ff-only origin main
Expand-Archive -LiteralPath "$HOME\Downloads\AUTOKOM-HA-PUSH-TARIFS.zip" -DestinationPath . -Force
git diff --check
git add -- docs/AUTOKOM-HA-PUSH-TARIFS.md platform scripts tests
git diff --cached --stat
git commit -m "fix: verify HA HTTPS cutovers and track GitHub push builds"
git push origin main
```

Sur la VM AUTOKOM, une fois les publications en cours terminées, appliquer la mise à niveau une fois.

```bash
sudo autokom update &&
sudo autokom devsecops apply --deploy --auto-notify &&
sudo autokom iac ha delivery --apply
```

## Vérifier le push GitHub

Dans AUTOKOM, vérifier la branche de publication et « Déploiement sur push ». Pour le projet portfolio, sélectionner le profil Vite et le répertoire de sortie `dist` si le profil enregistré est encore React/CRA. La branche `fix/vite-devsecops` contient déjà une migration. Pour corriger `main` au lieu de changer de branche, le patch `PORTFOLIO-VITE-FE190519.patch` s’applique à `fe190519` dans le dépôt **portfolio**.

```powershell
git switch main
git pull --ff-only origin main
git apply --check "$HOME\Downloads\PORTFOLIO-VITE-FE190519.patch"
git apply --index "$HOME\Downloads\PORTFOLIO-VITE-FE190519.patch"
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run build
git diff --cached --check
git commit -m "fix: migrate broken React build to Vite"
git push origin main
git rev-parse HEAD
```

Laisser la fiche du projet ou l’historique ouvert au moment du push sur la branche configurée. Ne pas cliquer sur « Déployer maintenant ». La nouvelle publication doit apparaître avec « Push GitHub · webhook ». En période de faible activité, le worker prend en charge la file lors de son prochain cycle. Si une publication est déjà en cours pour cet environnement, le webhook est conservé et traité à la suite. Les vues actives sont actualisées toutes les cinq secondes.

```bash
sudo autokom app backend github push-doctor --repository Jennylady/portfolio --commit SHA_DU_PUSH
```

La preuve recherchée est un webhook `push` `HANDLED` pour le SHA attendu et une publication liée `origine=WEBHOOK_SIGNÉ` avec `Jenkins=B:<numéro>`. `Jenkins=Q:<numéro>` signifie qu’elle est encore en file Jenkins. `RATTRAPAGE_HEAD` prouve une publication automatique par vérification périodique, mais pas la réception directe du webhook. Si aucun événement ne remonte, vérifier l’abonnement `push` et la livraison du SHA dans l’onglet **Advanced → Recent deliveries** de l’application GitHub. Ne pas utiliser `--apply` pour ce test.

## Vérifier la bascule publique HTTPS

Le contrôleur installé change uniquement l’enregistrement A du sous-domaine AUTOKOM éligible, sur une publication autonome HA déjà validée, et non le wildcard ni le CNAME ACME. Il vérifie deux réplicas applicatifs dans des zones distinctes, le TLS GCP avec le vrai nom du site, la version servie et le serveur principal de repli. Il confirme la bascule seulement après observation de la nouvelle adresse dans les deux résolveurs publics. Si la version ou son placement changent, la publication cloud précédente est préservée pendant le retour DNS au serveur principal.

```bash
sudo autokom iac ha delivery --status
sudo autokom iac ha apps --ha-only
site=portfoliolady.auto.ikom.mg
dig @1.1.1.1 +short A "$site"
dig @8.8.8.8 +short A "$site"
curl --noproxy '*' -fsS --max-time 15 "https://$site/" -o /dev/null \
  -w 'HTTP %{http_code} · IP %{remote_ip} · TLS verify=%{ssl_verify_result}\n'
```

Résultat attendu pour ce portfolio après bascule `PUBLIC_HTTPS_ACTIVE`, `35.186.239.102`, HTTP `200`, `TLS verify=0`. Si l’adresse est encore `136.116.89.163`, le trafic public est encore sur le serveur principal, même si le test avec `--resolve` est vert. Le contrôleur ne modifie pas les domaines externes, les déploiements groupés ni les applications avec variables ou secrets. Ne pas forcer le DNS pour contourner un prérequis non satisfait.
