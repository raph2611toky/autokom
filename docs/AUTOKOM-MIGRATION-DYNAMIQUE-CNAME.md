# AUTOKOM — Migration dynamique des domaines (Nginx + Cloudflare + HTTPS)

## Objectif

Ce correctif remplace le parcours *DNS seulement puis attente* par une migration par étapes, reprenable sans interruption de l'ancien domaine :

1. Découvrir les vhosts Nginx locaux, les upstreams nommés simples et leur port sur 127.0.0.1.
2. Choisir un service via le sélecteur natif AUTOKOM (↑/↓, Entrée), proposer un nouveau domaine et contrôler les collisions.
3. Vérifier la délégation réelle des nameservers Cloudflare et leur zone associée.
4. Vérifier que le service source écoute sur 127.0.0.1 et créer un **nouveau** vhost Nginx HTTP séparé (test `nginx -t`, reload contrôlé, rollback si échec).
5. Créer `origin.<zone>` en **A DNS-only** vers l'IP `AUTOKOM_INGRESS_IPV4`, et chaque nouveau nom en **CNAME DNS-only** vers ce point d'entrée commun.
6. Interroger directement les deux NS Cloudflare, de façon bornée ; attendre si nécessaire sans supprimer les fichiers déjà configurés.
7. Émettre un certificat Certbot avec HTTP-01 et mettre à jour uniquement le vhost créé, avec rollback Nginx si échec.
8. Contrôler HTTPS avec le bon Host/SNI puis enregistrer le résultat dans `/var/lib/autokom/domain-migrations/` (0600).

L'application doit pouvoir répondre au nouveau Host ; les callbacks OAuth, URL de backend, cookies, CORS et CSRF spécifiques **ne sont pas** reconfigurés arbitrairement.

## Exemple DNS

```text
origin.ikom.mg  A      56.228.86.160    DNS only

dia.ikom.mg     CNAME  origin.ikom.mg    DNS only
safidiko.ikom.mg CNAME origin.ikom.mg   DNS only
```

Un changement d'IP ne nécessite ensuite de modifier **que** le A de `origin.ikom.mg`, **après avoir préparé le nouveau serveur** et vérifié le proxy et les certificats. Cette commande ne change pas l'IP automatiquement.

**Attention :** ne pas utiliser `ikom.mg` comme point d'entrée CNAME ; il est proxifié Cloudflare, ce qui réactiverait potentiellement le proxy sur toute la chaîne. Un point `origin.<zone>` DNS-only évite ce mélange de politiques. Les sous-domaines web peuvent être proxifiés plus tard via une procédure séparée, avec TLS Full (strict) testé.

## Cas de DIA : A préexistant

Le premier essai a déjà créé `dia.ikom.mg A 56.228.86.160` dans Cloudflare. La nouvelle version :

- Ne détruit pas cet enregistrement lors du démarrage.
- Prépare le vhost HTTP, continue Certbot et HTTPS si les DNS sont corrects.
- Propose **après validation HTTPS** de remplacer ce `A` par `CNAME origin.ikom.mg`.
- Exige deux validations distinctes, dont le texte `CONVERTIR`.
- Tente de restaurer le A initial si Cloudflare refuse la création du CNAME ; en cas d'échec de restauration, l'administrateur doit intervenir immédiatement.

La conversion DNS (suppression puis création) peut introduire une courte fenêtre de changement ; par défaut le A existant est conservé. Ne pas lancer pendant une période critique.

## Commandes

Inventaire lecture seule :

```bash
sudo autokom domains migrate --check
sudo autokom domains migrate --audit
sudo autokom domains migrate --source dia.ik0m.tech --audit
```

Parcours complet interactif (sélection de service par flèches) :

```bash
sudo autokom domains migrate --apply
```

Parcours pour un site précis :

```bash
sudo autokom domains migrate --source dia.ik0m.tech --apply
```

Cartographie d'une autre ancienne zone, uniquement si la zone cible est associée au token Cloudflare :

```bash
sudo autokom domains migrate --from-zone ancien.example --to-zone nouveau.example --audit
```

Le suffixe cible est détecté depuis `/etc/autokom/secrets/cloudflare-dns.env`; une zone cible différente est validée contre l'API Cloudflare. Un nom spécifique peut être demandé dans l'interface ou via `--target` conjointement avec `--source`.

Si un contrôle DNS/Certbot/HTTPS n'est pas encore satisfait, relancer la même commande plus tard ; la configuration HTTP est conservée et le programme ne duplique ni CNAME ni vhost. Les certificats fonctionnels ne sont pas supprimés.

## Contrôles post-migration DIA

```bash
sudo nginx -t
sudo autokom dns cloudflare list
dig @1.1.1.1 +short A origin.ikom.mg
dig @1.1.1.1 +short CNAME dia.ikom.mg
curl -I https://dia.ikom.mg/
curl -I https://dia.ik0m.tech/
```

Le CNAME n'apparaît pas tant que la conversion du A n'a pas été confirmée. Ne pas supprimer l'ancien domaine ou ses enregistrements.

## Limites et prérequis

- Requiert accès root pour appliquer ; confirmations interactives obligatoires.
- Les upstreams multi-services, les directives FastCGI, les `root`/`alias`, les sites Docker avec DNS interne, les websockets à configuration particulière, les vhosts avec plusieurs `location` restent soumis à revue ; aucune copie aveugle d'une configuration Nginx existante.
- L'ancien site est maintenu, le vhost de migration est généré séparément dans `/etc/nginx/sites-available/autokom-migrate-<nom>.conf` et activé dans `sites-enabled`.
- Le backend Django principal, les OAuth callbacks, CORS/CSRF et les URLs internes des applications doivent être testés spécifiquement.
- La migration vers un nouvel IP/serveur nécessite que la cible soit prête, puis que l'enregistrement A de l'origine commune soit mis à jour dans Cloudflare ; aucune bascule IP silencieuse n'est effectuée.
- Ne pas déléguer `infra.ikom.mg` vers un seul NS si la disponibilité publique du DNS nécessite de la redondance.
- Aucun `.env` ni `.env.example` n'est inclus ou généré dans le dépôt.

## Régression

```bash
PYTHONPATH=scripts/lib python3 -m unittest discover -s tests -p 'test_autokom_*domain*.py' -v
python3 -m py_compile scripts/lib/autokom_domain_migration.py scripts/lib/autokom_domain_dns.py
```
