# AUTOKOM — Configuration Cloudflare, précontrôle de publication et liste de projets

## Diagnostic du 6 novembre 2026

Le pipeline Jenkins a réussi, mais le broker a refusé la publication (`AUTOKOM_DOMAIN_CLOUDFLARE_CONFIG_MISSING`). Cela **ne signifie pas** que Cloudflare est en panne : la configuration Cloudflare du service broker peut manquer, même si un autre outil sait interroger Cloudflare. Le DNS de `portfolio.auto.ikom.mg` n'a pas été créé, donc Nginx n'a pas activé le vhost public. Le relais Nginx *interne* Docker est distinct du vhost *public*.

Le broker lit désormais les identifiants de `/etc/autokom/secrets/cloudflare-dns.env` à chaque vérification, avec contrôle `root` et mode `0600` et refus des liens symboliques. Si ce fichier est absent, la compatibilité avec les variables systemd existantes est maintenue. Jamais de token dans la réponse au client, le journal ou le dépôt Git.

`domain.config.check` est exécutée avant que le worker n'envoie un job à Jenkins. Elle valide **localement** les prérequis DNS : présence de la zone et du token, cohérence du suffixe, IPv4 publique. Elle ne garantit pas les permissions du token chez Cloudflare, ni la propagation DNS, ni le défi ACME ; ces opérations restent vérifiées au moment de leur exécution.

## Contrôles sur Ubuntu (lecture seule)

```bash
sudo autokom dns cloudflare env show
sudo autokom runtime env show
sudo autokom runtime doctor
sudo systemctl status autokom-domain-reconciler.timer --no-pager
```

Si les identifiants sont absents, un administrateur les configure **une fois** en saisie masquée :

```bash
sudo autokom dns cloudflare env setup
sudo autokom runtime env setup
```

Ne jamais coller de jeton dans le terminal, un argument CLI, le chat ou Git. Les droits attendus sont **Zone DNS:Edit** et **Zone:Read** sur la zone `ikom.mg`. Le suffixe géré attendu est `auto.ikom.mg` et l'IPv4 ingress doit correspondre à l'instance AWS. Un jeton qui fonctionne ailleurs n'est pas forcément accessible au broker, et Cloudflare peut encore refuser des permissions insuffisantes.

La lecture du fichier sécurisé est désormais effectuée à chaque nouvelle opération : aucun redémarrage n'est requis **uniquement** pour relire ce fichier. `autokom update` et `runtime setup --apply` sont nécessaires pour déployer le nouveau code. Le timer de réconciliation traite les publications existantes sans relancer Jenkins ; pour observer :

```bash
sudo journalctl -u autokom-domain-reconciler.service --since '-30 minutes' --no-pager
sudo journalctl -u autokom-platform-broker.service --since '-30 minutes' --no-pager
```

Après vérification par AUTOKOM du DNS et du certificat, vérifier l'adresse depuis un autre réseau avec `curl -I https://portfolio.auto.ikom.mg/`. Ne jamais marquer une adresse comme prête sur la seule présence du conteneur Docker.

## Interface Mes projets

La liste est une seule rangée par projet : icône, nom/dépôt, statut coloré et icône paramètres adjacente à droite. Aucun long message d'activation DNS n'est affiché dans la liste. Le détail du projet, les publications et **Sites & domaines** conservent les informations HTTPS détaillées, et rendent le site cliquable seulement lorsque l'URL est vérifiée.

## Tests

```bash
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_cloudflare_runtime_preflight_20261106 tests.test_autokom_domain_publication_20261106 tests.test_autokom_runtime_broker
cd platform/frontend && npm test
```
