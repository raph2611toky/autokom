# AUTOKOM v28 — Authentification locale et gestionnaires de plugins

## Objectif

Le gestionnaire interactif peut sélectionner **tous** les résultats : 1–9 sur la
page, flèches gauche/droite pour passer d'une page à l'autre, haut/bas pour
naviguer, Entrée pour confirmer, `0` ou `q` pour annuler. La sélection d'un
plugin Jenkins ou Grafana démarre **effectivement** l'installation, sous une
confirmation finale et avec les permissions nécessaires. Aucune modification
n'est déclenchée par `search` sans `--select`, `list` ou `info`.

## Démarrage sécurisé (sur serveur Ubuntu, sous sudo)

```bash
sudo autokom auth               # assistant de configuration
sudo autokom auth smtp          # SMTP avec STARTTLS obligatoire
sudo autokom auth setup         # passphrase + confirmation e-mail + OTP
sudo autokom auth status
sudo autokom auth jenkins-token # nom utilisateur et jeton API Jenkins, une seule fois
```

Le serveur SMTP est validé par STARTTLS et authentification avant son
utilisation. L'utilisateur saisit deux fois sa **propre** adresse de récupération
et sa passphrase ; AUTOKOM envoie ensuite un code de confirmation de l'adresse.
**Sans SMTP fonctionnel, la récupération par e-mail n'est pas activée et
l'initialisation est refusée.** Le code a une durée de 10 minutes, cinq essais
maximum et un usage unique. Le mot de passe SMTP est enregistré séparément dans
un fichier root:0600 afin d'envoyer les messages ; il ne s'agit pas de la
passphrase AUTOKOM.

La passphrase AUTOKOM n'est jamais enregistrée en clair : hachage
PBKDF2-HMAC-SHA256, sel aléatoire de 128 bits, 600 000 itérations et comparaison
constante. Cinq échecs consécutifs provoquent un verrouillage de 15 minutes.
Les états et fichiers sont protégés par le répertoire `/etc/autokom/security`
(root:0700) et les fichiers root:0600 ; les writes sont atomiques. Ne pas
sauvegarder la passphrase dans un fichier, une variable d'environnement ou une
commande shell. Sauvegarder le répertoire en lieu sûr de façon chiffrée.

```bash
sudo autokom auth verify
sudo autokom auth change
sudo autokom auth reset          # OTP envoyé à l'e-mail vérifié
```

La passphrase AUTOKOM est un *second contrôle local* pour les opérations
sensibles, **pas un remplacement des permissions Unix, des droits Jenkins ni de
l'authentification Grafana**. En particulier Jenkins exige un jeton API distinct
avec ses propres privilèges ; `autokom auth jenkins-token` le conserve une fois
à `/etc/autokom/security/jenkins-api.auth` root:0600, puis le gestionnaire
réutilise ce fichier automatiquement. Le jeton API est stocké comme secret
réutilisable (donc pas haché), sans affichage ni passage dans les arguments
shell. Révoquer ou renouveler les jetons auprès du fournisseur en cas de fuite.

Avant configuration du contrôle AUTOKOM, les commandes historiques d'installation
continuent de fonctionner pour le bootstrap ; après configuration elles exigent
la passphrase pour les mutations depuis la CLI. La gestion de plugins exige
initialisation de l'authentification même si la configuration est nouvelle.
**Ce contrôle ne bloque pas un administrateur root malveillant ni les scripts
indépendants exécutés hors de la CLI AUTOKOM.** Un MFA résistant au phishing ou
un serveur central d'identité est nécessaire pour une protection multi-admin.

## Interface de plugins

```bash
autokom tools jenkins plugins                 # recherche, liste, mises à jour
autokom tools jenkins plugins search pipeline --select
autokom tools jenkins plugins list --filter git --select
sudo autokom tools jenkins plugins install git --apply
sudo autokom tools jenkins plugins upgrade --apply

autokom tools grafana plugins
autokom tools grafana plugins search logs --select
sudo autokom tools grafana plugins upgrade --check
sudo autokom tools grafana plugins upgrade --apply
```

Toutes les actions dangereuses effectuent une simulation lorsque `--apply` est
absent, et demandent confirmation avant la mutation. Jenkins ne redémarre jamais
automatiquement après installation/suppression. Pour Grafana, les plugins déclarés
avec `preinstall` restent protégés. Les contenus des catalogues proviennent des
sources officielles ; aucun plugin n'est inventé. Le choix final est déterminé
par la compatibilité effective des versions et des dépendances des services.

## Validation

```bash
autokom auth -h
autokom tools jenkins -h
autokom tools jenkins plugins -h
autokom tools grafana plugins -h
autokom tools show jenkins
```

Les reverse proxies dans `show jenkins` sont restreints aux vhosts propres à
Jenkins ou pointant réellement vers son upstream `127.0.0.1:8080`, pas aux
services métiers qui citent seulement Jenkins dans leurs configurations.
