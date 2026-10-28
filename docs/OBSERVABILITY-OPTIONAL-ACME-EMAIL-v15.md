# AUTOKOM — Contact Let’s Encrypt facultatif (v15)

## Parcours HTTPS

- Si Prometheus ou Grafana nécessite un nouveau certificat : une seule question
  `E-mail Let’s Encrypt (facultatif, Entrée pour ignorer)` pour toute la commande.
- **Entrée vide est valide.** AUTOKOM mémorise ce choix en mémoire pour le
  second domaine, sans le redemander.
- Un e-mail valide renseigné est partagé entre Prometheus et Grafana.
- Un format invalide peut être corrigé (trois saisies au maximum, compteur masqué).
- Aucun contact n'est demandé en `--check` ni pour un certificat déjà valide.
- Aucun e-mail n'est automatiquement enregistré dans les journaux.

## Arguments Certbot

Avec e-mail :

```text
certbot certonly --nginx --non-interactive --agree-tos --no-eff-email --email admin@example.org -d prometheus.example.org
```

Sans e-mail :

```text
certbot certonly --nginx --non-interactive --agree-tos --no-eff-email --register-unsafely-without-email -d prometheus.example.org
```

Ce dernier choix est pris en charge par Certbot mais **déconseillé par le projet** :
l'administrateur ne reçoit pas les avertissements par e-mail concernant le
renouvellement ou la révocation. Maintenir un contrôle automatisé de l'expiration
TLS et du timer `certbot` reste indispensable.

Le formulaire n'est pas censé se répéter pour Grafana si Prometheus a déjà
recueilli le choix durant le même lancement d'AUTOKOM. Un nouveau lancement
constitue une nouvelle session et peut redemander le contact si une première
émission est encore nécessaire.

## Reprise

Après la mise à jour du dépôt AWS :

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
```

Aucun service n'est modifié par `--check`. La commande `--apply` reste protégée
par les validations DNS, la vérification HTTP-01 et le contrôle des hôtes Nginx.
