# AUTOKOM — Souscription simulée pour valider l'option HA

## Périmètre

Ce correctif **n'intègre PAS un vrai prestataire de paiement**. Il crée des reçus
`AKTEST-…` et des droits TEST audités en base pour les seuls comptes clients
figurant dans la liste explicitement autorisée côté serveur. Aucun montant n'est
prélevé ni demandé à un service de paiement. **Ne pas activer pour tous les
utilisateurs en production.** Les droits TEST sont liés à un environnement ;
le compte utilisateur peut consulter leur historique.

- Découverte : inchangée, une instance, pas de souscription.
- Essentiel TEST : profil `autokom-essential-test`, une instance, 30/365 jours.
- Professionnel TEST : profil `autokom-pro-test`, deux instances autorisées, option HA active mais **non cochée par défaut**, 30/365 jours.
- Les demandes d'HA doivent toujours être explicites dans le formulaire et passer `check_entitlement`.
- Une attribution premium manuelle par administrateur ne peut pas être remplacée par une simulation.

## Installer et activer de manière contrôlée

Sur Kali, extraire directement le ZIP à la racine, committer uniquement son
contenu et pousser vers `main`. Sur le serveur Ubuntu principal :

```bash
sudo autokom update
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
```

**Activation exclusivement sur le serveur principal.** Par défaut, le mode est
PRODUCTION (aucun paiement simulé). Après déploiement du backend, activer avec :

```bash
sudo autokom app --mode-test
```

La première activation demande l'adresse e-mail du compte client vérifié
(sauf si `AUTOKOM_BILLING_SANDBOX_TEST_EMAILS` a déjà été configuré dans
`/etc/autokom/secrets/backend.env`). Plusieurs e-mails sont possibles, séparés
par des virgules. Cette liste est **obligatoire** ; elle ne devient jamais
universelle. La commande requiert root, la passphrase AUTOKOM si configurée,
crée une sauvegarde atomique du fichier, valide les paramètres, redémarre le
backend et vérifie que la nouvelle valeur est reçue par le processus.

```bash
sudo autokom app --mode-status  # Voir TEST/PRODUCTION et le nombre de comptes
sudo autokom app --mode-prod    # Désactiver la simulation immédiatement
```

`--mode-prod` ferme les endpoints de souscription simulée et empêche les
nouveaux déploiements d'utiliser des droits TEST. Les reçus anciens restent
conservés pour audit ; cette commande ne coupe pas les conteneurs déjà en
production, et ne change jamais `DEBUG`, `ALLOWED_HOSTS` ou les paramètres
TLS/DNS. Le backend est redémarré de manière contrôlée et l'ancienne
configuration est restaurée si le redémarrage échoue.

La configuration est dans `/etc/autokom/secrets/backend.env`, hors GitHub.
Les valeurs ne doivent pas être écrites dans `platform/backend/.env` ou un
fichier versionné.

## Test depuis la plateforme

1. Se connecter au compte client autorisé, propriétaire d'un projet (ou ADMIN de son organisation).
2. Sélectionner l'offre Professionnel sur la page de configuration.
3. Cocher « Haute disponibilité » **seulement pour le test à deux répliques**.
4. Cliquer « Activer Professionnel en mode TEST (0 Ar débités) ». Cette action crée le
   brouillon si nécessaire, enregistre un reçu simulé et attribue le plan au même
   environnement. Elle ne déclenche pas Jenkins.
5. Cliquer « Continuer » puis « Déployer » après la confirmation du reçu.
6. Contrôler les droits : `sudo autokom iac ha rights --list`, puis la livraison :
   `autokom iac ha delivery --status` et Grafana.

La simulation **n'assure pas en elle-même la disponibilité publique** : le mode
HA dépend encore des deux répliques saines, du routage cloud et d'un certificat
HTTPS valide, à vérifier séparément.

## API et sécurité

- `GET /api/billing/sandbox/availability/` : disponibilité pour l'utilisateur.
- `POST /api/projects/<uuid>/environments/<uuid>/billing/sandbox/checkout/`
  : formulaire `{ "plan":"pro", "period":"monthly", "idempotency_key":"<uuid>" }`.
- `GET /api/projects/<uuid>/environments/<uuid>/billing/sandbox/subscription/`
  : souscription courante sur cet environnement.
- `GET /api/billing/sandbox/account/subscriptions/` : historique propre au compte.

L'API ne prend **aucun numéro de carte**, ne fait **aucun appel bancaire** et
n'accepte **aucun montant choisi par le navigateur**. Elle vérifie le propriétaire,
l'email autorisé, l'idempotence et les droits existants ; le backend définit les
montants indicatifs, les ressources et la durée. Les reçus peuvent être révoqués
par un administrateur via l'action Django Admin.

## Contrôles avant déploiement

```bash
sudo autokom app deploy --check
# Tests ciblés sous le compte applicatif et base MySQL de test uniquement :
# /opt/autokom/venv/bin/python manage.py test apps.plans.entitlements.test_sandbox_checkout_20261110
```

**Retour à l'état fermé :** positionner
`AUTOKOM_BILLING_SANDBOX_ENABLED=false`, redémarrer `autokom-backend.service`
puis révoquer les anciens reçus de test par l'administration si nécessaire.
Les abonnements réels, lorsqu'un prestataire sera intégré, devront utiliser un
flux distinct avec validation serveur de transaction ou webhook signé ; ils ne
doivent jamais dépendre du code sandbox.
