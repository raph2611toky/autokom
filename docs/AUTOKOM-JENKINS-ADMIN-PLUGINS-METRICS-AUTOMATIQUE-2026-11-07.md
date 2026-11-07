# AUTOKOM — Premier administrateur Jenkins sans assistant Web

Date : 7 novembre 2026 — patch différentiel pour le dépôt privé AUTOKOM.

## Contexte

Sur la nouvelle VM GCP, le service Jenkins fonctionne et Nginx/HTTPS sont configurés,
mais la phase « Plugins et sécurité Jenkins » s'arrête pour afficher le déverrouillage
manuel de l'assistant Jenkins et la création manuelle du premier administrateur.
Cette étape est remplacée par une initialisation **locale, automatique et à usage unique**.

## Parcours d'installation

1. L'administrateur choisit le profil de plugins dans `sudo autokom install`.
2. Si et seulement si le contrôleur est encore dans le wizard officiel, sans
   utilisateur autre que l'administrateur temporaire du wizard, AUTOKOM génère un
   mot de passe fort dans `/etc/autokom/secrets/jenkins-first-admin.password`
   (root, mode `0600`) et un script Groovy de première initialisation.
3. Après accord de redémarrage déjà exprimé dans le choix d'installation,
   AUTOKOM redémarre Jenkins et vérifie que le script a créé `autokom-admin`,
   un jeton API distinct et le marqueur de fin de première installation.
   Le compte temporaire `admin` issu du wizard est supprimé.
4. Le jeton API `jenkins-admin.auth` (root, `0600`) sert à la vérification des
   droits administrateur et à l'installation des plugins Jenkins : aucun accès
   à l'interface Web ni copie manuelle du token n'est nécessaire.
5. Pour les profils contenant Matrix Authorization, AUTOKOM prépare une seconde
   initialisation au `safe-restart` après le chargement des plugins. Les droits
   `Overall/Administer` sont alors explicitement affectés à `autokom-admin`.
   Le résultat est vérifié avant de marquer la phase conforme.
6. Pour le profil DevSecOps avec Prometheus, AUTOKOM provisionne également un
   compte dédié `autokom-metrics` disposant de droits de lecture et
   `Metrics/View`, ainsi qu'un jeton de scraping conservé séparément du token
   administrateur. Prometheus utilise ce compte si `/prometheus/` requiert
   une authentification, sans copier le jeton administrateur.

## Garde-fous

- Aucune suppression, modification du realm ou rotation de mot de passe sur un
  contrôleur Jenkins **déjà initialisé** ou avec des comptes tiers.
- Aucun `--securityRealm=none`, compte anonyme administrateur ou mot de passe
  en argument de processus.
- Scripts de bootstrap installés uniquement en l'absence de conflits, et
  retirés après confirmation de réussite ; fichiers de secrets root:0600.
- Si une étape échoue, le redémarrage suivant ne réinitialise pas les
  identifiants ; les marqueurs de reprise restent disponibles.
- Les décisions de redémarrage sensible relèvent toujours de l'administrateur.
- Le mécanisme n'autorise pas de modification automatique des DNS, pare-feu,
  installations AWS, comptes Grafana ou autres applications existantes.

## Validation du déploiement

Sur GCP, après `sudo autokom update` puis `sudo autokom install`, le parcours
Jenkins doit montrer : création automatique du premier administrateur,
installation des plugins, safe-restart, contrôle Matrix, puis l'étape
Prometheus/Grafana. Le mot de passe initial peut être récupéré par **root**
à `/etc/autokom/secrets/jenkins-first-admin.password` pour une connexion
ultérieure ; AUTOKOM ne l'affiche jamais dans ses journaux.

Contrôles indépendants :

```bash
sudo systemctl is-active jenkins
sudo autokom status
sudo test -s /etc/autokom/secrets/jenkins-admin.auth && echo 'AUTH AUTOKOM OK'
sudo ls -l /etc/autokom/secrets/jenkins-first-admin.password
sudo journalctl -u jenkins -n 50 --no-pager
```

Les tests livrés couvrent la protection des fichiers, la mise en scène des
scripts, le respect des comptes existants, les marqueurs de reprise et le
chemin d'authentification des métriques. La simulation locale **ne remplace
pas** un test réel du démarrage Jenkins 2.580.2 sur la VM GCP, qui doit
confirmer les appels de l'API Jenkins et le chargement de Matrix Authorization.
