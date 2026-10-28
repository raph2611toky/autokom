# AUTOKOM v16 — observabilité opérationnelle et exploitation DevSecOps

## Périmètre

- **Prometheus** et **Grafana** existants : préservés ; ne pas réinitialiser les comptes.
- **Nginx** : les nouveaux vhosts AUTOKOM utilisent des `upstream` nommés. Les vhosts déjà gérés par AUTOKOM peuvent migrer **avec confirmation explicite**, après sauvegarde et `nginx -t`. Un vhost tiers est préservé.
- **Node Exporter** : proposition d'installation, bind `127.0.0.1:9100`, scrape Prometheus `job_name: node`, contrôle HTTP `/metrics` et absence d'écoute publique. Sur un déploiement existant non géré, refuser une migration risquée.
- **Alertmanager** : proposition d'installation, bind `127.0.0.1:9093`, clustering mono-instance désactivé (aucune écoute publique sur 9094), fichiers de règles AUTOKOM, intégration à `alerting.alertmanagers`, vérification `amtool check-config` + `promtool check rules`. Préserve les autres jobs et règles.
- **SMTP** : facultatif ; paramètres `email_host`, port TLS, `email_login`, `from_email`, destinataire `email_to`, mot de passe masqué. Le mot de passe réside dans `/etc/prometheus/.autokom-smtp-password` (root + groupe prometheus, permissions 0640) et n'est **jamais inséré dans YAML ou argv**. Les port 465 (TLS implicite), 587 et 2525 sont acceptés, avec exigence TLS. Tester l'envoi réel : la vérification de syntaxe ne prouve pas la livraison.
- **Login Prometheus** : sans fournisseur de sessions déployé, conserver HTTP Basic pour éviter d'afficher un faux formulaire HTML. AUTOKOM fournit un **adaptateur optionnel pour Authelia existant** : voir ci-dessous.

L'installation automatique traite uniquement les modules explicitement sélectionnés dans le formulaire. Le plan `autokom install --check` n'exécute ni APT ni modifications du serveur.

## Services et fichiers

```console
sudo autokom update
autokom tools list --all
autokom services show prometheus
autokom tools prometheus files
autokom tools prometheus cat prometheus-configuration
autokom tools prometheus tail access
autokom tools prometheus nano-end nginx-prometheus
sudo autokom tools prometheus nano nginx-prometheus
sudo autokom tools prometheus nano service-override
sudo autokom tools grafana nano grafana-configuration
```

Les alias `files` sont découverts à partir des fichiers présents, non de chemins devinés. Un alias de l'exemple peut ne pas exister sur toutes les installations. `nano-end` ouvre en fin de fichier. Une édition doit être exécutée avec `sudo`, confirmation et copie de sauvegarde datée. `nginx -t`, `promtool` et `amtool` sont appelés pour les fichiers concernés ; **aucun reload/restart automatique**. En cas de syntaxe invalide, restauration de la sauvegarde. Ne jamais éditer directement les données TSDB, les mots de passe, les clés ou les bases des applications.

## Variables d'environnement AUTOKOM

```console
autokom tools show prometheus
autokom tools prometheus env list
sudo autokom tools prometheus env set EXAMPLE_KEY
sudo autokom tools prometheus env delete EXAMPLE_KEY
autokom tools show prometheus -e
sudo autokom tools show prometheus -E
```

**Note :** utiliser `autokom tools show prometheus` (pas `tools prometheus show`), car `show` reste une commande catalogue. Une nouvelle valeur est saisie de façon masquée via TTY et enregistrée avec permissions `0600` dans `/etc/autokom/env/prometheus.env`, référencée par un drop-in dédié `99-autokom-env.conf`. Les clés d'environnement *tierces* ne sont pas modifiées en place. Seuls `prometheus`, `grafana`, `node-exporter`, `alertmanager` sont autorisés dans v16. Les changements prennent effet au prochain redémarrage contrôlé du service ; `-e` masque les valeurs et `-E` requiert sudo et confirmation de divulgation. Éviter les variables d'environnement pour les secrets critiques quand des fichiers de credentials systemd sont possibles.

## Alertmanager et métriques système

```console
sudo autokom install --check
sudo autokom install
sudo systemctl is-active prometheus-node-exporter prometheus-alertmanager
curl --noproxy '*' -fsS http://127.0.0.1:9100/metrics | head
curl --noproxy '*' -fsS http://127.0.0.1:9093/-/ready
sudo promtool check config /etc/prometheus/prometheus.yml
sudo promtool check rules /etc/prometheus/rules/autokom-alerts.yml
sudo amtool check-config /etc/prometheus/alertmanager.yml
```

Prometheus → `/targets` doit montrer `jenkins`, `node` et `prometheus` en **UP**. Les alertes par défaut `AUTOKOMJenkinsDown` et `AUTOKOMNodeDown` ont un délai `for: 5m` afin de réduire les faux positifs. Avant de promettre des notifications, valider la connectivité SMTP (sortie VPC/EC2, TLS, authentification, adresse de destination), puis générer une alerte de test encadrée. Des portails et Alertmanager non gérés ne sont jamais écrasés silencieusement.

## Page de connexion Prometheus — avec Authelia

**Une page de connexion n'est pas simplement un formulaire HTML.** Elle doit gérer cookies de session, CSRF, expiration, limites de tentatives, identités et accès conditionnel. AUTOKOM ne réimplémente pas ces fonctions dans Nginx.

Après avoir déployé et sécurisé **Authelia**, sa page de connexion HTTPS sur un sous-domaine cohérent avec `prometheus.ik0m.tech`, et son point local `127.0.0.1:9091/api/authz/auth-request`, exécuter :

```console
sudo autokom services prometheus auth authelia
```

La commande est **opt-in et fail-closed** : elle exige un code de refus `401` avec redirection HTTPS du portail, un vhost Prometheus effectivement géré par AUTOKOM, un terminal interactif et confirmation. Elle réalise `nginx -t` avant reload, puis restaure la configuration précédente en cas d'erreur. Les utilisateurs doivent ensuite se connecter sur le portail Authelia : la popup HTTP Basic disparaît **uniquement après une migration réussie**. Le mot de passe Basic antérieur n'est pas automatiquement migré dans Authelia ; définir préalablement les comptes et politiques d'accès. Le port `9091` d'Authelia doit rester privé.

### Important pour EC2

- **AWS Security Groups** et règles Docker : vérifier l'absence d'exposition directe des ports 8080, 9090, 9093, 9100 et 3000 ; seuls Nginx 80/443 et SSH autorisé devraient être publics.
- Le serveur déclare `ufw inactive`. Le catalogue `tools list` v16 lit désormais `ufw status`, pas l'état du service systemd ; AUTOKOM ne doit jamais activer UFW automatiquement sur une EC2 sans vérifier d'abord SSH, Docker et Security Groups.
- Le paquet Node Exporter utilise une unité systemd `prometheus-node-exporter`; Grafana utilise `grafana-server`, pas `grafana`.
- `systemctl is-active` et un code HTTP 200 ne prouvent pas que tous les jobs, destinataires SMTP ou utilisateurs du portail fonctionnent. Contrôler `/targets`, `/alerts`, le journal de livraison et l'accès navigateur.

## Limites de la version

Ce patch a été validé par tests unitaires sur l'arbre du projet, **pas sur l'EC2 de production**. L'installation d'Authelia elle-même, ses comptes et MFA ne sont pas inclus ; il s'agit d'un raccordement à un portail existant. Aucun changement DNS ou Security Group n'est réalisé. Les fichiers tiers, secrets et identifiants existants sont préservés.
