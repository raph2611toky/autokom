# AUTOKOM — Phase 2, validation des menus et domaines

## Réseaux

Le menu affiche deux choix : `127.0.0.1` ou `0.0.0.0`.

`0.0.0.0` signifie **écouter sur les interfaces IPv4**, non « autoriser tout le monde ».
L'isolation dépend également des autorisations SQL des comptes, des règles UFW
réellement appliquées, des Security Groups AWS/GCP et des publications Docker.
Le module `firewall.py` existant reste chargé et protège les ports internes.

Un CIDR n'est demandé **que pour créer un NOUVEAU compte SQL AUTOKOM**.
Il ne modifie pas les comptes existants. Entrer plusieurs plages avec une virgule,
un point-virgule ou un espace, par exemple :

`172.18.0.0/16, 172.19.0.0/16`

Tirets et underscores ne sont PAS des séparateurs valides.

Les questions comportent une explication jaune temporaire, effacée après
sélection/saisie.

## Jenkins et Nginx

`nginx -T` est inspecté en lecture seule, puis les hôtes exacts et le proxy
vers Jenkins (127.0.0.1:8080 ou upstream équivalent) sont identifiés.
Le certificat HTTPS présent est vérifié pour le domaine et sa validité
sur au moins 7 jours via OpenSSL.

- Domaine déjà opérationnel : réutilisation, aucun Certbot ni reload Nginx.
- Domaine déjà attribué à un autre service : refus sans modification.
- Certificat absent ou proche de l'expiration : arrêt avec diagnostic.
- Domaine nouveau : configuration Nginx + TLS disponible dans le module
  existant, seulement après validation DNS et des options fournies.

Ne jamais enregistrer le contenu intégral de `nginx -T`, ni les clés privées,
dans les logs de l'installateur.

## Application

À la racine publique `AUTOKOM/`, remplacer :

- `scripts/lib/services.py`
- `scripts/lib/menu.py`
- Ajouter `scripts/lib/nginx_discovery.py`
- `tests/test_phase2.py`
- Ajouter `tests/test_menu_domain_v3.py`

Vérification :

```bash
python3 -m py_compile scripts/lib/{services,menu,nginx_discovery}.py
python3 tests/test_phase2.py
python3 tests/test_menu_domain_v3.py
python3 tests/test_firewall.py
python3 scripts/lib/services.py --help
```

Ensuite commit/push, puis serveur Ubuntu :

```bash
curl -fsSLo /tmp/autokom-install.sh \
  https://raw.githubusercontent.com/raph2611toky/autokom/main/install.sh
sudo bash /tmp/autokom-install.sh --check --update --services
```

**Ne pas utiliser `--apply` avant d'avoir examiné le rapport, les règles AWS,
les comptes SQL existants et les redémarrages éventuellement nécessaires.**
