# AUTOKOM · Ressources recommandées et HTTPS GCP

Le frontend corrige le contraste du récapitulatif sur les parcours simple et
multiservice. Les paramètres distinguent le HTTPS du domaine public de la
validation HTTPS de la livraison GCP. Pour déployer le frontend depuis la VM,
une fois le correctif poussé sur main :

```bash
sudo autokom update &&
sudo autokom app frontend deploy --apply
```

## Diagnostic en lecture seule

Depuis une VM ou un poste disposant des droits de lecture GCP :

```bash
sudo autokom iac ha apps
sudo autokom iac ha delivery --status
sudo autokom iac ha logs --source delivery --lines 60

project=plucky-stacker-480421-c4
site=portfoliolady.auto.ikom.mg
lb_ip=35.186.239.102
gcloud compute forwarding-rules list --global --project="$project" \
  --filter="IPAddress=$lb_ip" --format='table(name,IPAddress,portRange,target)'
curl -sS --max-time 12 -o /dev/null -w 'HTTP GCP %{http_code}\n' \
  --resolve "$site:80:$lb_ip" "http://$site/"
curl -fsS --max-time 12 --resolve "$site:443:$lb_ip" "https://$site/" -o /dev/null
```

Le dernier test conserve le vrai nom du site pour TLS et le certificat.
Il contourne le DNS uniquement pour cette requête et ne désactive pas la
validation du certificat. Une règle sur le port 80 et des réplicas sains ne
prouvent rien sur le port 443.

## Préparer le certificat puis le port 443

Utiliser l'état Terraform GCS existant et une identité IaC habilitée. Ne pas
recréer le backend Terraform. L'API `certificatemanager.googleapis.com` doit
être activée sur le projet et l'identité IaC doit pouvoir gérer les ressources
Certificate Manager et le proxy HTTPS. Si un proxy HTTPS géré existe déjà,
vérifier ses domaines avant toute substitution d'un certificat classique.

1. Dans le `terraform.tfvars` existant de
   `/opt/autokom/source/terraform/environments/gcp-ha`, renseigner
   `https_dns_zone = "auto.ikom.mg"` et garder `enable_https = false`
   pour une première activation. Le plan de cette phase doit être limité à
   l'autorisation DNS, au certificat wildcard, à la certificate map et à
   son entrée. Ne pas appliquer un plan qui remplace le MIG ou modifie le
   routage existant. Utiliser `./manage.sh validate`, `./manage.sh plan`,
   `./manage.sh show` puis, si conforme, `./manage.sh apply`.
2. Lire `terraform output -json https_dns_record` dans le même dossier.
   Créer dans Cloudflare **uniquement** le CNAME indiqué par les champs
   `name` et `data`, en **DNS uniquement**. Le garder après émission pour
   le renouvellement. Ne pas modifier l'A/AAAA de `portfoliolady`, le
   wildcard de routage, ou les domaines de la plateforme. Si une politique
   CAA existe, vérifier qu'elle autorise Google Trust Services (`pki.goog`).
3. Attendre le statut `ACTIVE` du certificat et de l'entrée :

   ```bash
   gcloud certificate-manager certificates describe autokom-ha-wildcard \
     --location=global --project=plucky-stacker-480421-c4 \
     --format='yaml(managed.state,managed.authorizationAttemptInfo)'
   gcloud certificate-manager maps entries describe autokom-ha-wildcard-entry \
     --map=autokom-ha-tls-map --location=global \
     --project=plucky-stacker-480421-c4 --format='value(state)'
   ```

4. Passer `enable_https = true`, refaire le plan, puis vérifier et appliquer.
   Dans cette seconde phase, seul le proxy et la règle globale TCP/443
   doivent s'ajouter. Le proxy réutilise la même IP et la même carte de
   routage applicative que le listener HTTP. Vérifier avec le dernier
   `curl --resolve` ci-dessus et comparer le contenu à l'application
   attendue. Le wildcard couvre `portfoliolady.auto.ikom.mg`, pas
   `auto.ikom.mg` ni les noms à plusieurs niveaux comme
   `backend.portfoliolady.auto.ikom.mg`.

## Après le test 443

Le nouveau listener ne déplace pas la production. Le contrôleur actuel
continue d'indiquer que la **bascule publique** n'est pas validée et les
sites restent sur leur origine actuelle. Ne pas rediriger un domaine
géré manuellement. Le broker et le réconciliateur utilisent encore l'IP
d'ingress principale pour le DNS et sa validation. Une bascule par application
doit coordonner ces composants et prévoir le retour à l'origine existante.

En cas de problème sur le nouveau listener seul, repasser `enable_https = false`
et examiner le plan avant application. Conserver l'autorisation DNS et le
certificat si une nouvelle tentative est prévue. Ne pas supprimer la règle
HTTP ou le MIG pour retirer le listener HTTPS.

Les scénarios Terraform simulés se lancent avec Terraform 1.7 ou plus
récent. Dans une copie de travail sans état GCP, exécuter
`terraform init -backend=false`, puis `terraform test` dans
`terraform/environments/gcp-ha`. Ils utilisent un fournisseur simulé.
