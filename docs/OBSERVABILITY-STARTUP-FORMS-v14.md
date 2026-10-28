# AUTOKOM — Observabilité : démarrage fiable et formulaires regroupés (v14)

Ce correctif **s'applique directement sur AUTOKOM v13**. Il remplace uniquement `scripts/lib/observability.py` et ajoute tests et documentation. Les services, données et configurations existants restent préservés.

## Problème constaté

Dans la sortie EC2, AUTOKOM affiche `Prometheus : absent`, puis `Aucun service sur le port 9090` immédiatement après le lancement. Un `systemctl restart` réussi n'implique pas que Prometheus ait terminé de démarrer. Il pourrait aussi s'agir d'une erreur de configuration, de stockage, de permission ou de paquet : les journaux EC2 sont indispensables pour établir la cause réelle.

## Installation corrigée

- Si Prometheus est absent, AUTOKOM installe les paquets, contrôle son YAML via promtool et démarre le service.
- Le répertoire TSDB est créé et attribué à l'utilisateur `prometheus` seulement s'il est absent. Un répertoire de données existant n'est pas changé.
- Attente bornée de l'écoute locale et de `http://127.0.0.1:<port>/-/ready` avec HTTP 200. Une absence temporaire de port est tolérée ; une écoute `0.0.0.0` ou `*` est refusée immédiatement.
- En cas d'échec prolongé, message de diagnostic avec état systemd et commandes permettant d'inspecter les logs. Aucune disponibilité fictive n'est déclarée.
- `--check` n'effectue aucune modification et ne demande aucun secret.

## Formulaires regroupés par service

### Prometheus

1. Domaine HTTPS facultatif, contrôlé dès sa saisie (DNS/IP de l'instance, trois tentatives sans compteur).
2. E-mail Let’s Encrypt si un nouveau certificat est nécessaire ; cet e-mail sera réutilisé pour Grafana.
3. Compte HTTP Basic et mot de passe masqué, uniquement si un nouvel accès HTTPS Prometheus doit être initialisé.

### Grafana

1. Domaine HTTPS facultatif, contrôlé dès sa saisie.
2. E-mail TLS si besoin et non déjà demandé.
3. Administrateur et mot de passe masqué pour un premier démarrage, **avant toute installation**.
4. Installation existante : conserver les comptes par défaut ou choisir explicitement un changement de mot de passe dans la section Grafana.

Les secrets restent en mémoire pendant l'opération et ne sont ni stockés dans les paramètres INI, ni imprimés. Avant toute modification, une application non interactive sans les secrets indispensables est refusée.

## Commandes sur AWS

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
```

Si Prometheus ne démarre pas :

```bash
sudo systemctl status prometheus --no-pager
sudo journalctl -u prometheus -n 50 --no-pager
sudo promtool check config /etc/prometheus/prometheus.yml
sudo ss -lntp | grep ':9090'
```

Ne divulguez pas des journaux contenant des secrets ou des données internes. Prometheus reste sur `127.0.0.1` et n'est pas exposé publiquement sur le port 9090.

Les **tests locaux utilisent des simulations** ; aucun déploiement complet n'a été exécuté sur l'EC2 réel lors de la préparation du correctif.
