# AUTOKOM — diagnostic et reprise d'un conflit Nginx sur TCP/443 (GCP)

## Problème observé le 7 novembre 2026

La validation DNS-01 Cloudflare a obtenu les certificats et les services
Prometheus (`127.0.0.1:9090`) et Grafana (`127.0.0.1:3000`) fonctionnent.
En revanche, une tentative Nginx a journalisé plusieurs fois :

    bind() to 0.0.0.0:443 failed (98: Address already in use)

Les vérifications SNI en local échouent sur `127.0.0.1:443` et `10.128.0.4:443`.
Le HTTP/80 répond `200` au lieu de rediriger vers HTTPS.

Il faut distinguer quatre choses : configuration Nginx valide, service systemd,
propriétaire *réel* du socket TCP et comportement HTTP/HTTPS. Une vérification
`nginx -t` ou un `systemctl reload nginx` réussi ne suffit pas pour affirmer
que le nouveau listener 443 a été chargé.

## Comportement du correctif

- `autokom install` conserve les choix, comptes, certificats et données.
- À la fin de la phase observabilité, la vérification SNI et la redirection
  HTTP/HTTPS sont toujours obligatoires.
- Si la vérification échoue, AUTOKOM inspecte les listeners TCP 80 et 443 avec
  `ss` et relève l'identité du processus propriétaire.
- Si 443 est déjà occupé (Nginx autonome, Docker, Apache, autre), AUTOKOM ne
  stoppe **aucun processus** et présente le conflit : les applications en
  production sont préservées. Un listener sans informations de propriétaire
  est considéré occupé, jamais libre.
- Si 443 est libre et qu'aucun service étranger n'occupe 80, AUTOKOM valide
  `nginx -t`, tente un reload, vérifie 443, puis autorise au plus **un restart**
  systemd si le port reste libre. Les vérifications réelles HTTP et TLS sont
  relancées après la réparation. L'installation échoue si elles restent KO.
- `autokom tools nginx status`, `autokom tools prometheus status` et
  `autokom tools grafana status` indiquent aussi les propriétaires TCP.
- Aucune modification du pare-feu VPC GCP, de UFW ou de Cloudflare n'est
  nécessaire pour un `ConnectionRefusedError` local sur le port 443. Les
  contrôles d'accès externes restent distincts.

## Vérification de l'occupant des ports sur GCP (lecture seule)

    sudo ss -ltnp | grep -E '(:80|:443)[[:space:]]'
    sudo systemctl status nginx --no-pager -l
    sudo systemctl show nginx -p MainPID -p ActiveState -p SubState
    sudo nginx -t
    sudo journalctl -u nginx -n 80 --no-pager
    sudo autokom tools nginx status

Si le propriétaire est `docker-proxy`, identifier le conteneur avec :

    sudo docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Ports}}'

Si le propriétaire est un master Nginx, comparer les PID avec `MainPID` :

    ps -eo pid,ppid,user,args | grep '[n]ginx: master'

Ne pas utiliser `kill -9`, `pkill nginx`, `fuser -k 443/tcp` ni supprimer des
volumes. AUTOKOM refusera d'interrompre un service externe inconnu.

## Tests (Kali)

    PYTHONPATH=scripts/lib python3 -m unittest -v \
      tests.test_autokom_nginx_443_collision_recovery_20261107 \
      tests.test_autokom_nginx_tools_reliable_20261107 \
      tests.test_observability \
      tests.test_autokom_cloudflare_dns01_certbot_20261107

## Limites

Les tests unitaires valident la logique de récupération, pas l'identité
réelle du processus qui occupe actuellement 443 sur la VM GCP. Seule la
commande `ss` exécutée sur cette VM permet de l'identifier. Le VPC GCP et
l'accessibilité depuis une machine extérieure doivent être vérifiés après
l'activation du listener HTTPS local.
