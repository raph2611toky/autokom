# AUTOKOM — Contrôle préalable cohérent des domaines gérés

## Anomalie d'origine

`AUTOKOM_DOMAIN_SUFFIX_MISMATCH` après un pipeline Jenkins `SUCCESS`, avec un domaine réservé `portfolio.auto.ikom.mg`. Le précontrôle DNS précédent ne vérifiait pas le suffixe *des domaines réellement réservés* ; il vérifiait seulement la présence des identifiants et de la zone Cloudflare.

## Correctif

- Le worker transmet **avant tout dispatch Jenkins** le suffixe DNS Django et les noms des domaines gérés pour l'environnement concerné.
- Le broker refuse une divergence entre suffixes Django/runtime, un hôte en dehors de la zone autorisée ou une absence de contact ACME, avec un code sûr.
- Le broker relit `/etc/autokom/secrets/runtime.env` sur les opérations DNS/HTTPS pour prendre en compte les modifications approuvées sans conserver un environnement systemd périmé. Le fichier doit appartenir à `root` avec aucun droit pour groupe/autres et ne doit pas être un lien symbolique.
- `autokom runtime doctor` compare aussi le suffixe présent dans la configuration backend lorsqu'il existe.
- Aucune création DNS, commande Jenkins, suppression de conteneur ou désactivation du contrôle de propriété n'est effectuée pendant le précontrôle.

## Vérifier et aligner une fois la configuration sur Ubuntu

Exécuter en lecture seule :

```bash
sudo autokom runtime env show
sudo autokom app backend env show
sudo autokom dns cloudflare env show
```

Le domaine réservé `portfolio.auto.ikom.mg` exige un suffixe géré `auto.ikom.mg` **dans les deux configurations**. La zone Cloudflare autorisée doit rester `ikom.mg`, avec une IP ingress publique correcte. Si le suffixe du runtime est différent :

```bash
sudo autokom runtime env edit AUTOKOM_MANAGED_DOMAIN_SUFFIX
```

Saisir `auto.ikom.mg`. Si le suffixe du backend est différent, le corriger *uniquement après avoir confirmé que c'est bien la zone exploitée* :

```bash
sudo autokom app backend env edit AUTOKOM_MANAGED_DOMAIN_SUFFIX
```

Ensuite, appliquer les services versionnés :

```bash
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom runtime setup --check
sudo autokom runtime setup --apply
sudo autokom runtime doctor
```

Les modifications des paramètres autorisés ne doivent jamais être faites en éditant le code, en exposant un jeton Cloudflare ni en utilisant un suffixe générique pour contourner l'appartenance à la zone. `autokom runtime doctor` vérifie le fichier sans afficher les valeurs sensibles. Le rattrapage DNS/HTTPS des applications déjà actives reste assuré par le timer `autokom-domain-reconciler.timer` ; une nouvelle compilation Jenkins n'est pas nécessaire pour cette réconciliation.

Examiner les résultats sans publier de secrets :

```bash
sudo systemctl status autokom-domain-reconciler.timer --no-pager
sudo journalctl -u autokom-domain-reconciler.service --since '-30 min' --no-pager
sudo ls -l /etc/nginx/sites-enabled/autokom-customer-portfolio.auto.ikom.mg.conf
```

Ne déclarer le site disponible publiquement qu'après confirmation du DNS Cloudflare, de l'activation TLS et de la réponse HTTPS.

## Tests unitaires ciblés (sur Kali)

```bash
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_domain_suffix_preflight_20261106 \
  tests.test_autokom_cloudflare_runtime_preflight_20261106 \
  tests.test_autokom_domain_publication_20261106 \
  tests.test_autokom_runtime_broker
```

Cette livraison ne corrige pas les tests historiques sans rapport avec la publication DNS. Ne pas interpréter l'absence de nouvelles régressions comme une suite générale entièrement verte.
