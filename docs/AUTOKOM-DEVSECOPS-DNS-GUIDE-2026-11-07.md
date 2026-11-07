# AUTOKOM — Stages DevSecOps et guide DNS dynamique

## Objectif

Ce patch a été préparé depuis les sources AUTOKOM incluant le correctif d'activation du domaine `e52d021b1f`. Il contient uniquement les fichiers ajoutés ou modifiés. Les informations de DNS sont lues depuis le broker privé déjà en place ; aucun jeton Cloudflare n'est renvoyé au navigateur.

## DevSecOps

- `DEVOPS` : Jenkins récupère le dépôt et lance le build, les phases `sast` et `sca` sont **SKIPPED** volontairement.
- `DEVSECOPS` : le helper exécuté dans l'agent isolé lance obligatoirement Semgrep (SAST) et Trivy (vulnérabilités et secrets) avant le build. Un outil absent, des règles illisibles ou un scanner en échec **interdit** la validation du stage.
- Les règles Semgrep approuvées sont installées par le rôle Ansible `platform_runtime` dans `/usr/local/share/autokom-security/client-semgrep-rules.yml` (root:root, 0644). Le builder isolé ne reçoit aucun accès au répertoire des secrets `/etc/autokom/secrets`.
- `runtime doctor` contrôle la possibilité d'exécuter `semgrep --version` et `trivy --version` en tant qu'`autokom-builder`, et la lisibilité des règles. Ce diagnostic **ne prétend pas** scanner une application réelle.
- Les événements de scan apparaissent dans le journal technique. L'outil ne marque le stage `SUCCESS` que si la commande scanner réussit ; il le marque `FAILED` si l'étape est refusée.

**Limite importante :** ces vérifications hors serveur ne confirment pas que Semgrep/Trivy sont installés ou opérationnels sur le Jenkins de production. Une exécution réelle d'un projet choisi en mode DevSecOps est nécessaire pour confirmer leurs deux stages. Semgrep est initialement configuré avec un jeu de quatre règles approuvées basiques, non un audit de sécurité exhaustif. Des règles supplémentaires devront être revues et versionnées.

## Domaines et guide DNS

1. Sur « Sites & domaines », choisir `Domaine personnel (DNS externe)` ou `Sous-domaine AUTOKOM (géré automatiquement)`.
2. Le client envoie explicitement `domain_type` à l'API Django, comme l'exige `DomainCreateSerializer`.
3. L'API de liste de domaines inclut `dns_setup` pour un utilisateur autorisé : `ingress_ipv4`, `managed_suffix`, `dns_zone`. La valeur provient du fichier de configuration runtime via le broker root, jamais de l'IP du navigateur et jamais d'une constante JavaScript.
4. Dans le formulaire, le bouton « Télécharger le guide DNS » produit un fichier **HTML autonome** ouvrable hors ligne (et imprimable en PDF depuis un navigateur). Il indique la cible A, le suffixe, la zone, le TTL/proxy recommandé et, pour un domaine personnalisé déjà enregistré, l'enregistrement TXT de propriété.
5. Quand les paramètres DNS ne sont pas disponibles, le bouton est désactivé et aucun faux guide comportant une IP inventée n'est livré.
6. L'adresse devient cliquable uniquement après validation HTTPS par le backend existant. Aucune modification des permissions Cloudflare, de la politique DNS ni du mécanisme Certbot n'est introduite par ce patch.

## Installer dans Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-DEVSECOPS-GUIDE-DNS-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_devsecops_dns_guide_20261107 \
  tests.test_autokom_domain_suffix_preflight_20261106 \
  tests.test_autokom_cloudflare_runtime_preflight_20261106 \
  tests.test_autokom_buildkit_preflight
(cd platform/frontend && npm test && npm run build)
git diff --check
git status --short
```

Vérifier le diff puis ajouter **uniquement les fichiers concernés** (`git add -u` et `git add` des nouveaux fichiers concernés, pas `git add .` en présence d'autres travaux).

## Déployer depuis AUTOKOM sur Ubuntu après push Git

```bash
sudo autokom update
sudo autokom runtime setup --check
sudo autokom runtime setup --apply
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom runtime doctor
```

Le déploiement n'a pas besoin de création manuelle de DNS/Nginx : le mécanisme de domaines existant continue de fonctionner de manière autonome.

## Vérification réelle dans l'interface

Créer/finaliser une configuration en choisissant **DevSecOps** avant le lancement, puis publier un projet de test :
- `source` : succès ;
- `sast` : Semgrep exécuté et succès (ou échec explicite si code dangereux) ;
- `sca` : Trivy vuln + secrets exécuté et succès (ou échec explicite si finding bloquant) ;
- `build` : démarré **uniquement après** les deux contrôles réussis ;
- `release` et `health` : successifs, selon le DNS, Nginx et HTTPS existants.

Ne pas utiliser un succès Jenkins d'un projet configuré en **DevOps** comme preuve que Semgrep et Trivy ont fonctionné : dans ce mode, leur statut `SKIPPED` est normal.

## Validation du patch hors serveur

- 42 tests Python ciblés réussis, couvrant le runner, les réponses DNS, les vérifications de sécurité et la création du guide.
- 82 tests Node du frontend réussis.
- Fichiers Python compilés et `LiveSites.jsx` parsé avec le compilateur TypeScript.
- Build Vite non exécuté : dépendance locale absente de cet environnement (à valider sur Kali).
- Suite Python complète interrompue dans l'environnement de test ; la version de référence avait déjà 6 échecs et 5 erreurs. Ne pas déclarer la suite générale verte.
