# AUTOKOM — récupération automatique DNS / HTTPS

## Contrat fonctionnel

L’opérateur ne doit pas exécuter `systemctl start` pour achever une publication.
Le service `autokom-domain-reconciler.service` est exécuté automatiquement par :

- `autokom-domain-reconciler.timer` : 45 s après activation et ensuite environ toutes les 2 minutes (avec temporisation aléatoire) ;
- `autokom-domain-reconciler.path` : dès qu’un des fichiers de configuration runtime, Cloudflare ou backend change.

Les deux unités sont installées et activées de façon idempotente par `autokom runtime setup --apply`.

## Garantie et limites

Le réconciliateur ne relance **jamais Jenkins** et ne touche pas aux domaines personnalisés sans preuve de propriété.
Il ne prend en charge que les domaines AUTOKOM gérés d’une version en cours d’exécution, reconnue dans MySQL et dans l’état du broker. Les opérations Nginx/Certbot sont verrouillées avec les déploiements, rollbacks et suppressions de runtime.

Les défaillances temporaires sont réessayées, sans secrets en logs, avec un espacement d’au moins cinq minutes par domaine. L’ancienne version fonctionnelle reste servie tant qu’un nouveau déploiement échoue.

Une configuration autorisant une autre zone, une absence de droits Cloudflare, une impossibilité de validation ACME ou une ouverture HTTP publique manquante ne peut pas être contournée automatiquement sans modifier la politique de sécurité ou l’infrastructure extérieure. La réparation reprendra dès que les prérequis seront restaurés.

`autokom runtime doctor` contrôle désormais l’état des unités .timer et .path : il ne peut plus déclarer entièrement valide une automatisation qui n’est pas activée.

## Déploiement

1. Sur Kali : appliquer uniquement les fichiers de cette archive dans la branche correspondant au commit de référence, exécuter les tests, examiner le diff, puis commiter et pousser.
2. Sur Ubuntu : `sudo autokom update` puis `sudo autokom runtime setup --check` et `sudo autokom runtime setup --apply`. Le timer et le watcher sont installés et démarrés par AUTOKOM, sans étape supplémentaire.
3. `sudo autokom runtime doctor` valide leurs états automatiquement.

Cette archive ne contient aucun identifiant, secret Cloudflare ou certificat.
