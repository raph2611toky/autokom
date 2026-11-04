# Migration ik0m.tech vers ikom.mg — audit sécurisé avant application

Cette mise à jour ajoute `autokom domains migrate --audit`, entièrement en lecture seule.

```bash
sudo autokom domains migrate --audit
sudo autokom domains migrate --source dia.ik0m.tech --audit
```

Si Nginx contient plusieurs proxys, une application statique, un upstream Docker ou un site complexe, le rapport liste la raison du refus. La commande `--apply` **ne contourne pas** ces restrictions : lorsque toutes les configurations sont non éligibles, elle s'arrête sans écrire dans Cloudflare ou Nginx.

## Approche de migration

1. Vérifier `dig NS ikom.mg +short` (doit correspondre aux NS attribués par Cloudflare) et la validité de la zone Cloudflare.
2. Exécuter `sudo autokom domains migrate --audit` pour choisir un premier service pilote non critique.
3. Pour un site classé « revue manuelle nécessaire », étudier **uniquement les causes remontées** et adapter une stratégie de migration par service ; ne jamais recopier aveuglément un vhost HTTPS, des upstreams, la configuration OAuth, les cookies ou les secrets.
4. Pour un site confirmé éligible, prévisualiser `--source ... --check`, puis appliquer `--source ... --apply` avec confirmation. AUTOKOM crée un vhost et un DNS **parallèles**, conserve l'ancien domaine et prévoit HTTPS.
5. Vérifier le nouveau domaine et l'application (authentification, sessions, callbacks OAuth, WebSockets, CORS, routes API) avant de basculer le frontend ou les utilisateurs.

Pour la migration du backend et des frontends AUTOKOM, vérifier aussi les URL de callback GitHub App, CSRF/CORS et autres intégrations externes ; elles ne changent pas automatiquement avec Nginx.

Les indicateurs « API Cloudflare accessible » et « BIND opérationnel » ne prouvent pas que Jenkins, le runtime client ou les certificats des nouveaux noms sont déjà prêts.

Aucune configuration secrète n'est incluse ou modifiée par ce patch.
