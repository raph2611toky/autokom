# AUTOKOM — Gestion intégrée des sites, domaines et projets

Version différentielle établie sur `AUTOKOM-SOURCES.zip` fourni par l'utilisateur.

## Changements

- Domaine AUTOKOM toujours réservé à la création, même lorsqu'un domaine personnalisé a été demandé ; si un ancien projet n'en possède pas, son domaine géré est ajouté à la publication suivante.
- Après un BuildKit et un démarrage Docker sains, le worker demande à son broker la création DNS Cloudflare, la vérification DNS et l'activation Certbot/Nginx du sous-domaine géré **avant** le commit de publication. Un échec bloque cette nouvelle publication et sollicite le rollback existant. Le site n'est pas présenté comme publiquement prêt sans HTTPS.
- Les domaines personnalisés doivent toujours prouver leur propriété DNS ; ils ne sont pas activés de force.
- Choix du domaine principal sans couper l'ancien ; désactivation explicite d'un domaine secondaire ; nom et preuve conservés pour éviter toute reprise non autorisée.
- Adresse HTTPS validée visible dans « Mes projets », détail du projet, publication, historique et « Sites & domaines ». Aucun lien cliquable n'est forgé si l'état n'est pas ACTIVE/HTTPS.
- Archivage et restauration ; suppression logique confirmée par le nom exact, restreinte au propriétaire/admin, qui arrête les conteneurs et retire les vhosts reconnus par le broker. L'historique MySQL et la réservation du domaine restent conservés à des fins de sécurité et d'audit.
- Sûreté des opérations hôte : socket Unix AUTOKOM autorisé, chemins dérivés de l'identifiant, marqueur de propriété du domaine, liste explicite, verrou runtime, restauration Nginx sur échec de recharge.

## Conditions préalables importantes

Avant le premier déploiement avec URL automatique, la configuration runtime et celle de Django doivent désigner **le même suffixe géré** (p. ex. `auto.ikom.mg`) et une IPv4 publique stable. Le broker doit disposer des droits Cloudflare pour la zone correspondante et d'un e-mail ACME valide. Le port public 80 doit être accessible pour la validation HTTP-01, et 443 pour le site. Des contrôles DNS/TLS ne peuvent pas être « contournés » automatiquement.

Le serveur reste responsable des DNS : il ne faut pas créer manuellement un faux lien dans React. Des conflits de noms Cloudflare bloquent par sécurité plutôt que d'écraser un enregistrement non autorisé. Les délais de propagation DNS sont réessayés de façon bornée, sans forcer l'activation.

## Intégration depuis Kali

L'archive contient **uniquement** les fichiers ajoutés ou modifiés. Depuis la racine du dépôt :

```bash
unzip -o /home/raph/Downloads/AUTOKOM-PROJETS-SITES-DOMAINES-PATCH-2026-11-06.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_project_lifecycle_20261106 tests.test_autokom_runtime_broker tests.test_autokom_internal_ingress
cd platform/frontend && npm test && npm run build && cd ../..
python3 -m compileall -q platform/backend/apps scripts/lib/autokom_platform_broker.py
git diff --check
git status --short
```

Examiner le diff et confirmer que seules les sources autorisées ont changé, puis `git add` les fichiers nécessaires, `git commit` et `git push origin main`.

## Déploiement contrôlé sur Ubuntu

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

`autokom app deploy` exécute les migrations Django versionnées avant le redémarrage du service. Exécuter cette opération dans une fenêtre de maintenance : le schéma doit être migré avant d'exposer le nouveau frontend et avant toute nouvelle publication cliente.

## Vérifications de bout en bout

1. Créer un projet ; vérifier la présence d'un domaine géré dans l'API (`PENDING`).
2. Publier ; vérifier le BuildKit, le conteneur, la transition du domaine vers `ACTIVE`, l'HTTPS et le lien accessible depuis un autre réseau.
3. Ajouter un domaine custom, configurer son TXT et A, l'activer, puis le déclarer principal. L'ancien doit rester disponible jusqu'à sa désactivation.
4. Archiver puis restaurer un projet (l'archivage bloque les nouvelles publications mais ne dépublie pas l'existant).
5. Supprimer un projet de test avec confirmation exacte et vérifier l'absence de vhost actif/du conteneur tout en conservant son audit.
6. Simuler un Nginx refusé, un domaine appartenant à un autre environnement et un Cloudflare indisponible. Les opérations doivent échouer sans modifier un vhost tiers et sans inventer un lien public.

## Limites de la validation effectuée hors production

La validation locale a exécuté la compilation des Python, les tests Node frontend disponibles et les tests runtime spécifiques. Les dépendances Vite/React nécessaires à `npm run build` n'étaient pas présentes dans le conteneur d'analyse, et Django/MySQL de test n'étaient pas disponibles ; la migration et le déploiement réel restent à valider sur l'environnement équipé. La suite Python générale présentait déjà des échecs identiques dans l'archive de départ ; ils doivent être traités séparément pour une certification globale.

**Remarque sur la « suppression » :** ce patch réalise une suppression logique du projet (il disparaît des listes), avec retrait du service et du routage. Il ne purge pas l'historique de la base ni n'efface arbitrairement les entrées DNS Cloudflare ou les certificats : ces ressources restent réservées pour éviter une réattribution dangereuse. Une purge irréversible et conforme à une politique de rétention demanderait un workflow séparé.
