# AUTOKOM · Domaine HTTPS : validation dès la saisie (v13)

## Portée du correctif

Le module `scripts/lib/observability.py` appelle le validateur
`domain_validation.py` **avant de passer au domaine suivant** :

1. Nom DNS complet sans schéma HTTP/HTTPS, port ni chemin ;
2. Résolution des enregistrements DNS A / AAAA publics ;
3. Conformité des A avec l'IP publique directe EC2 obtenue via IMDSv2 (ou `--public-ip` explicite lorsque les métadonnées sont indisponibles) ;
4. Conformité des AAAA avec les adresses IPv6 publiques réellement assignées ;
5. Interdiction d'utiliser le même domaine pour Prometheus et Grafana.

Si une saisie échoue, l'erreur s'affiche (A/AAAA différent de l'IP EC2, absence
DNS, nom invalide, doublon), puis **la même question est reposée**. Trois
saisies sont permises **sans afficher de compteur de tentative**.
Après trois échecs, le module retourne une erreur explicite. Aucune installation,
redémarrage, écriture de configuration ni demande de secret n'est lancée à ce stade.

Un contrôle DNS complet est effectué une seconde fois dans `preflight_domains`
juste avant les étapes système, pour détecter les changements DNS entre-temps.
L'accessibilité HTTP publique n'est pas prouvée par ces contrôles : elle dépend
des Security Groups AWS, d'UFW, du routage, des règles Nginx, et est validée
par le défi HTTP-01 de Let's Encrypt lorsqu'un nouveau certificat est émis.

**Note** : le profil réseau est une EC2 avec IP publique directe. Les domaines
derrière CDN/ALB nécessitent un autre profil. Ne pas désactiver la validation
pour contourner la différence entre l'IP publique de l'EC2 et l'IP d'un proxy.

## Installation dans le dépôt

Depuis la racine du dépôt AUTOKOM, extraire le ZIP. Il contient uniquement les
fichiers nouveaux ou modifiés par ce correctif :

- `scripts/lib/observability.py`
- `scripts/lib/domain_validation.py` (inclus pour compatibilité si v12 absent)
- `tests/test_observability_domain_activity_v12.py`
- `tests/test_domain_entry_validation_v13.py`
- `docs/OBSERVABILITY-DOMAIN-ENTRY-v13.md`

Vérifier sans toucher à AWS :

```bash
python3 -m py_compile scripts/lib/observability.py scripts/lib/domain_validation.py
python3 -m unittest discover -s tests -p 'test_*.py'
git diff --check
```

Sur EC2 après `git push` : `sudo autokom update`, puis
`sudo autokom install --check` ou `sudo autokom install`.

**Attention aux noms** : `ik0m.tech` contient le chiffre **zéro**, alors que
`ikom.tech` contient la lettre **o**. AUTOKOM n'essaie jamais de corriger
silencieusement ce type d'erreur.
