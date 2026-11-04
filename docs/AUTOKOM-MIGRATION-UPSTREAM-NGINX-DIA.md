# AUTOKOM — migration pilote : upstream Nginx local

## Portée

Ce correctif élargit uniquement l'inventaire et la migration parallèle vers `ikom.mg` aux vhosts utilisant un **unique upstream Nginx local** (un seul `server 127.0.0.1:PORT;` et éventuellement `keepalive N;`), tel que celui de DIA :

```nginx
upstream dia_ik0m_tech_backend {
    server 127.0.0.1:8464;
    keepalive 64;
}
server {
    server_name dia.ik0m.tech;
    location / { proxy_pass http://dia_ik0m_tech_backend; }
}
```

Les upstreams multiples, distants, à configuration dynamique, les sites statiques et les emplacements multiples **restent exclus**. On ne copie jamais les directives Nginx du client. Le nouveau vhost AUTOKOM pointe uniquement sur le port local identifié. Le script vérifie aussi que le domaine cible n'existe pas déjà dans un autre vhost Nginx (y compris avec wildcard).

## Kali : intégrer et tester

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM_Domain_Nginx_Upstream_DIA_DIRECT.zip
python3 -m unittest discover -s tests -p 'test_autokom_domain*.py' -v
python3 -m unittest discover -s tests -p 'test_autokom_env_domain_migration.py' -v
python3 -m py_compile scripts/lib/autokom_domain_migration.py
git diff --check
git add scripts/lib/autokom_domain_migration.py tests/test_autokom_domain_named_upstream.py docs/AUTOKOM-MIGRATION-UPSTREAM-NGINX-DIA.md
git diff --cached --check
git commit -m 'fix: safely migrate local named nginx upstreams'
git push origin main
```

## AWS : audit, puis migration DIA uniquement

```bash
cd /opt/autokom/source
sudo autokom update
sudo nginx -t
sudo autokom domains migrate --source dia.ik0m.tech --audit
curl -sS -o /dev/null -w 'Upstream DIA HTTP %{http_code}\n' --connect-timeout 3 --max-time 8 -H 'Host: dia.ik0m.tech' http://127.0.0.1:8464/
```

L'audit doit indiquer `prêt (proxy local)` et `127.0.0.1:8464 (upstream nommé)`.

Avant de lancer la migration, vérifier que `dia.ikom.mg` n'est pas déjà attribué à une autre application. L'outil refuse désormais un domaine déjà déclaré dans Nginx ou présentant une collision Cloudflare.

```bash
sudo autokom domains migrate --source dia.ik0m.tech --apply
```

Dans le sélecteur AUTOKOM choisir la création du nouveau domaine en parallèle, puis saisir `MIGRER` lorsque demandé. Le processus tente dans cet ordre : pré-vérification, création A **DNS only** dans Cloudflare, attente de propagation si nécessaire, ajout de vhost HTTP distinct, certificat Certbot `webroot`, HTTPS, vérification HTTP. **L'ancien domaine et son certificat restent inchangés.**

Si `Propagation DNS en cours` ou `Certificat non obtenu` apparaît, ne supprimez pas l'ancien vhost. Corriger DNS/ports 80-443, puis réexécuter la même commande. Aucun `reset`, purge Nginx ni changement BIND n'est requis.

## Vérification après création

```bash
dig @1.1.1.1 +short A dia.ikom.mg
sudo nginx -t
curl -I --max-time 12 https://dia.ikom.mg/
curl -I --max-time 12 https://dia.ik0m.tech/
```

Note : l'API de DIA, les cookies, OAuth et CORS ne sont pas modifiés par ce correctif. Tester toutes les fonctions applicatives avant d'envisager une éventuelle redirection de l'ancien domaine vers le nouveau.

## Limites / sécurité

- Une seule route `location /` avec un seul `proxy_pass` vers un seul upstream local autorisé.
- Aucun transfert de fichiers secrets, aucune commande shell issue d'un vhost.
- Le rollback Nginx existant (`nginx -t` puis reload) est conservé.
- Le certificat ne peut être délivré que si DNS et HTTP-01 atteignent réellement l'hôte.
- La zone `infra.ikom.mg` de BIND9 n'est pas concernée.
- Les autres domaines classés complexes restent en revue manuelle jusqu'à analyse de leurs vhosts réels.
