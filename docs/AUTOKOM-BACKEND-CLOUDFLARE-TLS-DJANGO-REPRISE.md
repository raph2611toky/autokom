# AUTOKOM · TLS Cloudflare Free et migration backend reprise

## Diagnostic confirmé et zones de responsabilité

`backend.auto.ikom.mg` est un sous-domaine profond (deux labels sous `ikom.mg`).
Sur une zone Cloudflare DNS complète, Universal SSL gratuit couvre `ikom.mg`
et `*.ikom.mg`, mais **pas** `backend.auto.ikom.mg`. Un CNAME proxifié
vers l'apex `ikom.mg` aboutit à Cloudflare, dont le certificat Edge peut ne pas
couvrir le nom, même lorsque Let's Encrypt a créé un certificat Nginx valide.
Erreur visible: `SSL_ERROR_NO_CYPHER_OVERLAP`.

Second problème distinct: après une mise à jour de `/etc/autokom/secrets/backend.env`,
le précédent `autokom app backend domain --apply` redémarrait `autokom-backend`
puis interrogeait immédiatement Gunicorn une seule fois. Il pouvait conclure
trop tôt à un échec et restaurer l'ancienne configuration. Le correctif attend
maintenant un maximum de 30 secondes. Cela ne remplace pas le diagnostic d'un
vrai crash Django (journalctl).

## Préparation non destructive sur Ubuntu

```bash
cd /opt/autokom/source
sudo nginx -t
sudo systemctl is-active autokom-backend
sudo ls -l /etc/nginx/sites-enabled/autokom-backend-domain-backend.auto.ikom.mg.conf
curl --noproxy '*' --resolve backend.auto.ikom.mg:443:127.0.0.1 \
  -i https://backend.auto.ikom.mg/api/health/
sudo journalctl -u autokom-backend -n 80 --no-pager
```

**Ne pas afficher les `.env`, les jetons API ou les mots de passe** dans les logs,
les tickets ou GitHub. Les commandes ci-dessus ne divulguent pas leurs valeurs.

## Installation du correctif depuis Kali

Le ZIP contient uniquement `scripts/lib/autokom_backend_domain.py`,
`tests/test_autokom_backend_cloudflare_tls_fix.py` et ce guide. Il respecte
l'arborescence de la racine AUTOKOM, ne touche ni Django ni la CLI centrale,
ni aux fichiers `.env` ni aux services du serveur pendant extraction.

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o ~/Downloads/AUTOKOM_Backend_Cloudflare_TLS_Health_Retry_DIRECT.zip
PYTHONPATH=scripts/lib python3 -m unittest discover -s tests -p 'test_autokom_backend*.py' -v
python3 -m py_compile scripts/lib/autokom_backend_domain.py
git diff --check
git add scripts/lib/autokom_backend_domain.py \
  tests/test_autokom_backend_cloudflare_tls_fix.py \
  docs/AUTOKOM-BACKEND-CLOUDFLARE-TLS-DJANGO-REPRISE.md
git diff --cached --check
git diff --cached --stat
git commit -m 'fix: Cloudflare deep hostname TLS and Django startup readiness'
git push origin main
```

## Reprise AWS

```bash
cd /opt/autokom/source
sudo autokom update
sudo autokom app backend domain --check
sudo autokom app backend domain --dns-mode a --apply
```

L'option `--dns-mode a` est facultative pour un hostname profond : c'est le
nouveau défaut automatique. En mode interactif, utiliser `A DNS-only`, puis
`Ajouter ... en parallèle`; confirmer `MIGRER` et, si le CNAME préexiste,
`REMPLACER`. Le changement DNS est fait **après** le contrôle TLS local.
Le programme garde l'ancien backend `backend.auto.ik0m.tech`, son service,
MySQL, les secrets, le frontend et les URLs OAuth historiques.

Un CNAME existant est reconnu et conservé jusqu'au contrôle du certificat et
jusqu'à la réussite du health check sur `127.0.0.1` avec SNI. Si Cloudflare
refuse la création du A, AUTOKOM tente de restaurer le CNAME précédent ; si
la restauration échoue, il demande une intervention sur la zone DNS.

## Vérifications après reprise

```bash
sudo autokom app backend domain --status
sudo autokom app status
sudo nginx -t
curl --noproxy '*' --resolve backend.auto.ikom.mg:443:127.0.0.1 \
  -i https://backend.auto.ikom.mg/api/health/
dig @1.1.1.1 +short A backend.auto.ikom.mg
curl -i https://backend.auto.ikom.mg/api/health/
curl -i https://backend.auto.ik0m.tech/api/health/
```

Sur le DNS-only `A`, la résolution doit renvoyer `56.228.86.160` (tant que
l'IP AWS n'a pas changé), et non les IP anycast de Cloudflare. Le port 443/TCP
AWS doit être ouvert et le certificat public Let's Encrypt valide. La présence
d'un certificat local ne garantit pas l'absence de filtrage réseau externe.

Si Django ne démarre pas dans les 30 secondes, consulter :

```bash
sudo journalctl -u autokom-backend -n 100 --no-pager
sudo autokom app doctor --no-repair
```

## Autre option

Pour conserver le proxy Cloudflare, utiliser `backend.ikom.mg` (un seul niveau)
ou prévoir un certificat **Edge Cloudflare** couvrant expressément
`backend.auto.ikom.mg` via une option appropriée. Le certificat Let's Encrypt
sur AWS ne remplace pas le certificat Edge.

Documentations Cloudflare :
- https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/
- https://developers.cloudflare.com/ssl/troubleshooting/version-cipher-mismatch/
- https://developers.cloudflare.com/dns/proxy-status/
