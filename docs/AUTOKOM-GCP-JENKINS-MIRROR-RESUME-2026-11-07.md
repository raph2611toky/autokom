# AUTOKOM — reprise de l'installation Jenkins sur GCP

## Incident traité

Sur Ubuntu 24.04.5 GCP, l'installation Jenkins LTS 2.580.2 avec le dépôt officiel `https://pkg.jenkins.io/debian-stable` échoue au téléchargement du paquet depuis le miroir `mirror.dst.ca`. Ce message seul ne permet pas de déterminer si le miroir est indisponible, lent ou refuse la connexion. L'installation antérieure a déjà pu créer `/etc/systemd/system/jenkins.service.d/20-autokom-loopback.conf`.

## Correction

Dans `ansible/roles/jenkins_server/tasks/main.yml` :

1. Détecter le fichier systemd déjà posé par AUTOKOM. Comparer son contenu exact à celui attendu ; poursuivre s'il correspond, ne jamais écraser une configuration étrangère ou un lien symbolique.
2. Réessayer l'installation du paquet Jenkins via APT et le dépôt LTS officiel.
3. Si la récupération APT échoue, utiliser `autokom_jenkins_deb_fallback.py`, qui trouve le candidat et son empreinte SHA-256 / sa taille dans les métadonnées de l'index APT signé.
4. Essayer successivement les miroirs HTTPS `mirrors.xmission.com` et `mirror.ossplanet.net` pour **la même version** du paquet. Refuser tout téléchargement dont la taille ou le SHA-256 diffèrent ; nettoyer les fichiers partiels ; refuser les redirections HTTP.
5. Installer la copie vérifiée par `ansible.builtin.apt` avec ses dépendances, puis continuer les tâches Jenkins et MySQL habituelles.

Le script de secours n'exécute jamais `dpkg --force-*` et ne désactive pas les signatures APT ou les validations TLS. Il n'est invoqué que si l'installation normale échoue.

## Application sur Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-JENKINS-MIRROR-RESUME-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_jenkins_verified_mirror_20261107 \
  tests.test_phase2 \
  tests.test_jenkins_postinstall \
  tests.test_jenkins_onboarding \
  tests.test_autokom_gcp_jenkins_acme_prompt_20261107
git diff --check
# Revoir les changements, puis commit/push des 4 fichiers du ZIP.
```

## Application sur GCP

```bash
sudo autokom update
sudo autokom install
```

Reprendre les sélections de l'administrateur : MySQL, accès Docker Jenkins, domaine `jenkins.ik0m.tech`, Certbot et e-mail ACME. Ne pas basculer Cloudflare/AWS pour résoudre un problème de miroir APT.

## Contrôles de reprise

```bash
sudo autokom status
sudo systemctl status jenkins --no-pager
sudo journalctl -u jenkins -n 80 --no-pager
```

AUTOKOM doit pouvoir terminer l'installation même si le miroir initial est défaillant, à condition qu'un des miroirs alternatifs soit joignable et qu'un paquet valide existe dans les métadonnées APT. Si tous les miroirs échouent, l'échec doit rester explicite et préserver les configurations existantes.

## Tests

9 nouveaux tests ciblent la comparaison de métadonnées, la sélection de miroirs, la vérification d'intégrité, les redirections HTTPS et l'idempotence du fichier systemd. Les tests de composants Jenkins existants sont également exécutés. Pas de test réel de téléchargement 51 Mo ni d'installation Jenkins sur GCP depuis l'environnement de préparation.
