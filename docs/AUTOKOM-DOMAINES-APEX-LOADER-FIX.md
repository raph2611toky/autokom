# AUTOKOM — Migration vers la racine `@`, vérification HTTPS et loader dynamique

Correctif incrémental à extraire **depuis la racine du dépôt**. Aucun `.env` ou `.env.example` n'est fourni. Aucun serveur, service ou secret n'est modifié par l'extraction du ZIP.

## Pourquoi l'étape « HTTPS non validé » apparaissait

Sur une EC2, `curl --resolve dia.ikom.mg:443:56.228.86.160` lancé *depuis la même instance* peut échouer sur le chemin vers son adresse publique NAT, même si le vhost et le certificat sont valides. Le contrôle d'origine utilise désormais :

```bash
curl --noproxy '*' -Iv --resolve dia.ikom.mg:443:127.0.0.1 https://dia.ikom.mg/
```

Il vérifie le certificat présenté par Nginx, le bon SNI et le `Host` HTTPS. Les réponses HTTP 404/5xx ne valident pas le service. AUTOKOM enregistre séparément la validation TLS d’origine et le fait que la validation publique reste à effectuer. Cela ne remplace pas le contrôle public à faire séparément (ex. depuis Kali). Si l'application retourne un code inattendu ou si TLS échoue, la migration reste en attente et l'ancien domaine fonctionne.

## DNS : plus d'`origin.ikom.mg` automatique

La zone Cloudflare `ikom.mg` possède déjà un `A` racine pointant vers l'IP d'ingress. AUTOKOM réutilise **uniquement** cet enregistrement, sans le créer, le supprimer ni modifier son statut proxy. Un nouveau sous-domaine devient `CNAME <nom> -> ikom.mg`, après validation de l'IP du `A` de la racine via l'API Cloudflare.

**Attention :** actuellement `ikom.mg` est **Proxied** dans Cloudflare. Un CNAME pointant vers cette racine l'est effectivement aussi, même s'il a l'icône « DNS uniquement ». Le correctif présente ce comportement avant confirmation et crée les nouveaux CNAME dans le même mode. Il **ne désactive pas** le proxy de la racine pour forcer un fonctionnement DNS-only. Certaines applications, ports non HTTP ou configurations SSL/TLS particulières peuvent nécessiter des adaptations et une vérification publique.

Pour `dia.ikom.mg` déjà en `A`, AUTOKOM garde cet enregistrement, termine la vérification HTTPS locale, puis ne propose sa conversion qu'avec confirmations. La modification de type DNS n'est pas atomique ; la restauration de l'ancien A est tentée si Cloudflare refuse de créer le CNAME.

`origin.ikom.mg` créé par l'ancienne version **n'est pas supprimé automatiquement** par une migration. Sa présence ne déploie ni serveur ni service supplémentaire et ne consomme qu'une entrée DNS. Nettoyage explicite :

```bash
sudo autokom domains prune-origin --check
sudo autokom domains prune-origin --apply
```

La suppression est refusée si un CNAME Cloudflare référence encore `origin.ikom.mg`, si l'enregistrement diffère de celui prévu ou si l'utilisateur ne saisit pas `SUPPRIMER-ORIGIN`. Vérifier tout autre usage externe ou applicatif avant de confirmer.

## Parcours de migration (par service, reprend sans doublons)

```bash
sudo autokom update
sudo autokom domains migrate --source dia.ik0m.tech --audit
sudo autokom domains migrate --source dia.ik0m.tech --target dia.ikom.mg --apply
sudo nginx -t
curl --noproxy '*' -Iv --resolve dia.ikom.mg:443:127.0.0.1 https://dia.ikom.mg/
```

Ensuite, **sur Kali** (hors du serveur AWS) :

```bash
curl -I https://dia.ikom.mg/
curl -I https://dia.ik0m.tech/
dig @1.1.1.1 dia.ikom.mg A +short
```

Ne pas supprimer l'ancien domaine, sa configuration Nginx, son certificat ou ses enregistrements tant que les parcours applicatifs (API, sessions, CORS, cookies et éventuels callbacks) n'ont pas été validés.

## Logs pendant l'opération

Chaque étape conserve le loader animé et trois dernières lignes d'exécution en vert. La sortie intégrale est écrite dans un fichier privé `0600` sous `/var/log/autokom/platform/domains-migrate-*.log`. Le loader est effacé entre les étapes ; les succès et les échecs restent visibles. Les jetons, secrets et en-têtes sensibles sont masqués dans l'aperçu. Le mode `--audit` reste en lecture seule.

## Limites

Nginx complexes, upstreams multiples, Docker interconteneurs et applications nécessitant plusieurs `location` restent en revue manuelle. Les certificats Cloudflare Universal SSL ne couvrent pas forcément des sous-domaines de profondeur arbitraire. Un certificat HTTPS Nginx local ne démontre pas que tout le trajet public à travers Cloudflare fonctionne. Les opérations réelles Cloudflare et Certbot n'ont pas été exercées depuis l'environnement de test ; tester d'abord `dia.ikom.mg`.
