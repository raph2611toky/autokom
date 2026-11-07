# AUTOKOM — Reprise idempotente de l'observabilité sur GCP

## Contexte réel

Sur GCP Ubuntu 24.04, Jenkins et ses plugins sont installés, Prometheus et Grafana aussi.
Les domaines `prometheus.ikom.mg` et `grafana.ikom.mg` pointent vers l'IPv4 publique.
Certbot a d'abord échoué sur un 404 puis, après activation du webroot ACME, AUTOKOM
avait refusé sa propre configuration temporaire avec :
`prometheus.ikom.mg: certificat existant invalide, correction automatique refusée`.

## Cause et correction

La version antérieure vérifiait `TLS PENDING` dans un bloc `server` extrait par
`nginx -T`. Or ce marqueur n'est présent que dans l'en-tête du fichier géré par
AUTOKOM, hors du bloc `server`. Le test était donc impossible à satisfaire
pour un fichier parfaitement légitime. La nouvelle reconnaissance porte sur :

- le fichier Nginx régulier exact, au nom prévu par AUTOKOM ;
- le lien symbolique actif, dirigé précisément vers ce fichier ;
- le contenu strictement égal à un modèle AUTOKOM de site temporaire : version
  historique HTTP 503 ou version HTTP 503 avec route ACME ;
- l'unique bloc serveur attendu pour un site temporaire.

Un site TLS complet n'est renouvelé automatiquement que si le contenu est
**exactement** celui généré par cette version d'AUTOKOM et si le certificat est
invalide/expirant. Les fichiers nginx étrangers, modifiés manuellement, ou
potentiellement ambigus ne sont pas écrasés.

La validation HTTP-01 utilise toujours le webroot dédié déjà introduit dans
la version précédente, avec test HTTP 200 du contenu exact avant Certbot.
L'installation reste marquée inachevée en cas d'échec réel ACME.

## Réponses et secrets conservés

La sélection non sensible est enregistrée sous
`/var/lib/autokom/install/observability-selection.json` (root:0600) : domaines,
choix de contact ACME y compris l'absence volontaire d'e-mail, compte Grafana,
Node Exporter, cAdvisor et Alertmanager, avec option SMTP activée ou non.
Ces choix sont persistés **avant les contrôles DNS et Certbot** afin de survivre
à un échec extérieur.

Après saisie valide dans le terminal, le secret SMTP est enregistré dans
`/var/lib/autokom/install/observability-smtp-private.json` (root:0600), jamais
imprimé ni transmis comme argument de commande. Les mises à jour du secret
sont atomiques, sans création d'une copie `.bak` en clair. La configuration
Alertmanager finale existante n'est pas remplacée à la reprise.

**Migration depuis une ancienne version :** les anciennes installations
n'enregistraient pas le mot de passe SMTP avant d'atteindre Alertmanager. Si
la première tentative a échoué sur TLS, ce secret doit être ressaisi **une
seule dernière fois**. Après ce premier enregistrement, les reprises le
réutilisent. Aucun secret absent ne peut être reconstruit à partir des logs.

Les choix déjà mémorisés ne sont pas redemandés lors de `sudo autokom install` ;
`sudo autokom configure observability` permet une reconfiguration explicite.
Le mot de passe administrateur Grafana existant est conservé sans demander
à chaque exécution s'il faut le changer.

## Procédure

Sur Kali, intégrer ce patch dans le dépôt, exécuter les tests, `git diff --check`,
puis publier sur la branche `main` du dépôt privé.

Sur GCP :

```bash
sudo autokom update
sudo autokom install
sudo certbot certificates
sudo nginx -t
sudo systemctl is-active prometheus grafana-server jenkins nginx
```

Aucune opération n'efface un compte, une clé, une zone Cloudflare, une
configuration Nginx étrangère, une TSDB Prometheus ou un dashboard Grafana.
Si un fichier Nginx ne correspond à aucun modèle AUTOKOM connu, l'installation
s'arrête avec un diagnostic plutôt que de le remplacer.

## Tests

- Détection réelle du fait que les commentaires `TLS PENDING` ne figurent pas
  dans les blocs `server` de Nginx.
- Reprise d'un placeholder HTTP 503 historique/sécurisé.
- Renouvellement sûr du template HTTPS AUTOKOM exact.
- Refus des configurations Nginx inconnues.
- Choix ACME sans e-mail enregistré, non redemandé.
- Choix d'exporters et SMTP persistés après échec ACME.
- Secret SMTP conservé à root:0600, liens symboliques et permissions non
  privées refusés.

La validation Python locale ne prouve pas l'obtention effective du certificat
sur GCP ; vérifier les logs du prochain `autokom install` pour confirmer.
