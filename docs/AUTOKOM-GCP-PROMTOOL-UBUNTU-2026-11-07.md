# AUTOKOM — Réparation Prometheus (`promtool` sur Ubuntu 24.04 / 26.04)

## Incident sur GCP

Sur `instance-20261028-090021`, Ubuntu 24.04, `sudo autokom install` a échoué pendant l'installation de Prometheus :

```text
apt-get a échoué (code 100) : E: Unable to locate package promtool
```

L'ancien code demandait systématiquement `apt-get install prometheus promtool`. Sur Ubuntu 24.04 LTS, le binaire `/usr/bin/promtool` est livré par le paquet `prometheus`. Sur d'autres versions (notamment Ubuntu 26.04), `promtool` est un paquet distinct.

## Correctif

1. Si Prometheus est absent, actualiser le catalogue APT et installer le paquet `prometheus`.
2. Si `promtool` n'est pas déjà disponible, interroger `apt-cache show --no-all-versions promtool` **après** `apt-get update`. Demander le paquet séparé uniquement s'il est publié dans le catalogue local.
3. Si Prometheus est déjà installé mais que `promtool` est absent, installer seulement le paquet `promtool` lorsqu'il est disponible ; ne pas réinstaller Prometheus inutilement.
4. Vérifier l'existence réelle de `promtool` après l'installation. Ne pas poursuivre la validation ni annoncer un succès si l'exécutable manque.
5. Ne modifier ni la configuration Jenkins, ni les paramètres TLS/Cloudflare/SMTP, ni les données TSDB existantes au titre de cette correction.

## Mise à jour du code sur Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-PROMETHEUS-PROMTOOL-APT-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_promtool_apt_noble_20261107 \
  tests.test_observability_install_flow_v14 \
  tests.test_observability_domain_activity_v12 \
  tests.test_observability_v17

git diff --check
git add scripts/lib/observability.py tests/test_observability_install_flow_v14.py \
  tests/test_autokom_promtool_apt_noble_20261107.py \
  docs/AUTOKOM-GCP-PROMTOOL-UBUNTU-2026-11-07.md
git commit -m "fix: install Prometheus promtool according to Ubuntu APT packaging"
git push origin main
```

## Reprise sur GCP

```bash
sudo autokom update
sudo autokom install
```

Contrôles après l'installation :

```bash
command -v prometheus
command -v promtool
promtool --version
sudo systemctl is-active prometheus grafana-server jenkins
sudo autokom status
```

Si le programme demande encore une valeur secrète après un échec : c'est parce que les secrets non encore appliqués ne sont pas mémorisés par cette version du formulaire. Les choix de domaines déjà sauvegardés sont réutilisés ; ne pas placer les mots de passe sur la ligne de commande ni dans le dépôt Git.

## Validation locale

9 nouveaux tests ajoutés. 936 tests sur le code corrigé contre 927 sur la version de référence ; les deux exécutent exactement les mêmes 12 tests en échec (7 `FAIL` et 5 `ERROR`), sans nouvelle régression détectée. L'installation réelle sur GCP et les requêtes HTTPS restent à valider sur la machine.
