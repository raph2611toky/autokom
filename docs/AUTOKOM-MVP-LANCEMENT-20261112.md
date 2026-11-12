# AUTOKOM MVP — patch progressif, 12 novembre 2026

## Avertissements et portée exacte

Ce patch ne procède à **aucune suppression** des conteneurs, domaines, volumes, secrets ou VM existants. Le mode MVP est désactivé par défaut (`AUTOKOM_MVP_ENABLED` absent). Le déploiement réel doit être effectué après sauvegarde et vérification de Django/React.

**Livré** : calcul des durées, droits temporaires gratuits à deux workers, accès DevOps et DevSecOps dans le contrôle backend commun, verrouillage par identifiant de compte GitHub + identifiant de dépôt après mise en file d'une première publication, comptes publics « offre gratuite de lancement », restriction du checkout simulé aux seuls comptes explicitement autorisés par UUID, commande de réconciliation *locale* des domaines expirés, correctif du durcissement systemd pour la synchronisation HA Snap.

**Non livré / BLOQUANTS avant publication publique complète** : paiement/renouvellement réel, purge automatique au bout de 24 h, suspension d'un environnement HA sur le Load Balancer GCP (la commande refuse de le toucher), timer déjà installé pour exécuter périodiquement la commande MVP, bascule HTTPS/DNS GCP, validation navigateur du formulaire de création et de toutes les versions de frameworks. Ne pas présenter ces fonctions comme opérationnelles.

Le code de configuration du frontend `Deploy.jsx` existant emploie encore le code interne `pro` comme interrupteur de disponibilité HA. L'API retourne **cet identifiant de compatibilité uniquement au client**, avec le libellé public gratuit et aucun reçu de paiement ; le backend impose en toute circonstance les quotas de `mvp-lancement` (750m CPU, 1024 Mo RAM, 2048 Mo stockage, 2 instances). **Ce n'est pas un abonnement premium payant.** Pour les comptes autorisés via la liste d'UUID, les API de simulation préexistantes restent accessibles en privé si `AUTOKOM_BILLING_SANDBOX_ENABLED` et leurs propres contrôles l'autorisent ; aucune interface de paiement simulé n'est exposée publiquement par ce patch. Cet accès privé n'est ni un paiement ni une preuve de facturation. L'exemption illimitée est portée par la liste d'UUID et non par un reçu de simulation.

## Règles métiers

- Frontend seul : 30 jours ; frontend seul avec HA : 15 jours.
- Frontend + backend, ou backend serveur seul : 15 jours ; avec HA : 7 jours.
- Première publication réussie = point de départ, jamais remis à zéro par un changement de configuration. Tout ajout de backend/HA **raccourcit** la durée à partir de l'origine.
- Projet et dépôt modifiables tant qu'aucune publication n'est en cours ou déjà réussie. Après première tentative mise en file, un autre dépôt est temporairement bloqué jusqu'à ce que la tentative échoue ; une publication réussie verrouille durablement le dépôt. Les projets supprimés logiquement restent dans l'historique.
- Utilisateur administrateur de soutenance exempté via une liste d'UUID côté serveur uniquement.
- À échéance : autorisation de nouvelles publications refusée. Le gestionnaire peut désactiver les domaines du reverse proxy **local** (pas GCP HA). Il conserve les conteneurs pendant et après les 24 h, par sécurité tant qu'un vrai paiement/renouvellement n'existe pas.

## 1. Installer dans le dépôt (machine de développement)

Le ZIP est directement extractible à la racine, sans répertoire enveloppant et sans installateur :

```bash
unzip -o AUTOKOM-MVP-LANCEMENT-20261112.zip -d /home/raph/SOUTENANCE/AUTOKOM
cd /home/raph/SOUTENANCE/AUTOKOM
python3 -m unittest discover -s tests -p 'test_autokom_mvp_*.py' -v
git diff --check
git status --short
```

Relire les changements, puis pousser sur `main`. Sur le serveur GCP principal, `sudo autokom update` met les sources à jour. Cela ne déploie pas le frontend ni le backend, et ne modifie pas le LB.

## 2. Vérifier le runtime Django et déployer progressivement

Avant activation, lancer les contrôles `python manage.py check` dans le **vrai environnement virtuel Django d'AUTOKOM**. Ne pas utiliser le Python système en dehors du venv.

Ensuite, après sauvegarde DB et révision du diff, déployer les services avec les commandes existantes de ton CLI (`autokom app backend deploy` et la commande frontend indiquée par `autokom app --help`). Vérifier les pages Compte/Offres et les endpoints de facturation. En cas de problème, ne pas activer `AUTOKOM_MVP_ENABLED`.

**Configuration de déploiement** : renseigner les variables suivantes dans l'environnement réel chargé par le service Django (`systemctl cat autokom-backend.service` pour vérifier `EnvironmentFile`/drop-ins). Elles ne sont **pas** les variables de `/etc/autokom/update.env`, qui sert au CLI GitHub :

```ini
AUTOKOM_MVP_ENABLED=1
# Date exacte de mise en service UTC ; protège les applications déjà lancées
AUTOKOM_MVP_ROLLOUT_AT=2026-11-12T00:00:00+00:00
# Une ou plusieurs valeurs d'UUID Django User, jamais des identifiants GitHub
AUTOKOM_MVP_UNLIMITED_USER_IDS=<UUID_UTILISATEUR_AUTORISE>
```

La valeur `AUTOKOM_MVP_ROLLOUT_AT` doit être changée pour la **véritable date d'activation**, en UTC, et propagée identiquement à tous les processus Django (API, worker, gestionnaire des échéances). Sa présence évite de compter les jours antérieurs pour les applications déjà publiées. Ne jamais la changer après le lancement, cela renouvellerait les durées. Il faut aussi vérifier les données PostgreSQL/MySQL de production ; aucune nouvelle migration n'est requise par les fichiers proposés, puisqu'ils réutilisent les modèles existants.

Pour obtenir l'UUID du compte de soutenance depuis un shell Django privé, rechercher l'utilisateur par son courriel après vérification de son identité ; ne jamais écrire cet UUID dans le frontend ou le dépôt.

## 3. Corriger le timer HA Snap

`AUTOKOM_HA_WORKER_SYNC_ERROR gcloud returned 1: cannot change profile for the next exec call: Operation not permitted` est compatible avec le blocage `NoNewPrivileges=yes` sur un exécutable gcloud fourni par Snap. Le nouveau service généré utilise `NoNewPrivileges=no`, sans modifier les règles de pare-feu.

Sur le serveur principal :

```bash
sudo python3 /opt/autokom/source/scripts/lib/autokom_ha_worker_sync.py --install-timer
sudo systemctl cat autokom-ha-worker-sync.service
sudo systemctl start autokom-ha-worker-sync.service
sudo journalctl -u autokom-ha-worker-sync.service -n 60 --no-pager
sudo autokom iac ha apps -o wide
```

Si l'exécution reste interdite par Snap, **ne pas contourner AppArmor** ; remplacer l'installation Snap de gcloud par une installation officiellement prise en charge, après vérification de compatibilité. Le bon état du timer ne démontre jamais, à lui seul, une synchronisation réussie.

## 4. Échéances : contrôle préalable sans effet puis suspension locale

Depuis le *venv* Django, et avec les variables MVP réellement chargées :

```bash
python manage.py autokom_mvp_reconcile
# À planifier via timer systemd seulement après validation d'une VM de staging :
python manage.py autokom_mvp_reconcile --apply
```

`--apply` désactive les domaines locaux d'un projet expiré et demande au broker de retirer ses routes Nginx. Les conteneurs continuent de tourner. Les projets HA sont ignorés tant qu'AUTOKOM ne dispose pas d'une action de retrait manifeste GCP vérifiée. Après 24 h, un message `PURGE_DUE` indique une intervention future requise, mais rien n'est effacé automatiquement. La commande doit être intégrée à un timer **distinct** et testée avec le même venv et le même `EnvironmentFile` que le backend avant activation en continu.

## 5. Matrice des recettes analysées dans le code existant

| Type | Profil autorisé | Limites vérifiées par l'analyse du code |
|---|---|---|
| Frontend | Vite (React, Vue, etc.) | build npm, `dist`, port 8080 |
| Frontend | CRA / npm statique | `build`/`dist`, port 8080 |
| Frontend | HTML statique | pas de build, port 8080 |
| Frontend | Next.js export statique | `out`, port 8080 |
| Frontend | Next.js SSR | runtime Node, `.next`, port 8080 |
| Backend | FastAPI / Flask / Django / WSGI | runtime Python, port 8000 |
| Backend | Express / NestJS | runtime Node, port 8000 |
| Backend | Spring Maven / Gradle | runtime Java, port 8000 |
| Backend | PHP simple / Laravel / Symfony | runtime PHP, port 8000 |
| Autre | Dockerfile personnalisé | support de configuration sous réserve de validation du pipeline |

Cette matrice décrit les **allowlists/contrats de configuration** lus dans le dépôt. Elle n'atteste **pas** que toute version de chaque framework fonctionne : pour cela il faut un dépôt d'essai par framework/version, tests CI, build, déploiement isolé, vérification HTTP et rollback. Buildpacks reste non pris en charge. Aucun test de bout en bout ni test de navigateur réel n'a été exécuté par cette archive.

## 6. Critères de validation avant publication et DNS

1. `python manage.py check` et migrations satisfaisantes ; tests du backend de production.
2. Création frontend seule → possibilité de publier, DevOps et DevSecOps ; durée enregistrée après succès.
3. Deuxième repo du même GitHub → 403 au lancement même après suppression du projet initial ; même repo → autorisé.
4. Ajouter backend via `ServiceStack` au même repo → autorisé ; expiry 15/7 jours sans réinitialisation.
5. Option HA → deux workers réellement `HEALTHY 2/2`, renouvellement d'une release, et pas de bascule si 0/2 ou 1/2.
6. À expiration, suspension uniquement du reverse proxy local ; test négatif prouvant absence de route via autres domaines. HA GCP attend un correctif distinct.
7. HTTPS certifié sur LB `35.186.239.102`, contrôle Host/HTTP, sauvegarde et retour arrière DNS vers `136.116.89.163` avant bascule.
8. Accès administrateur illimité uniquement pour UUID explicitement autorisé.

La présence de conteneurs applicatifs sur le principal reste voulue tant que HTTPS et DNS du Load Balancer ne sont pas validés. Le nom `mode test` n'est pas affiché sur les pages Compte, Tarifs et Checkout incluses dans le correctif ; d'autres composants historiques non modifiés pourraient encore employer cette terminologie.
