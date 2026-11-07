# AUTOKOM — Installation Jenkins sur GCP : nouvelle saisie de l’e-mail ACME

## Incident reproduit

Sur Ubuntu 24.04.5 (GCP), l’administrateur a choisi MySQL local, la création du compte `autokom_app`, l’accès Docker Jenkins confirmé, le nom `jenkins.ik0m.tech` et Certbot. Lorsque l’e-mail ACME était laissé vide, `scripts/lib/services.py` levait une `ValueError` et terminait l’étape Jenkins. Cette validation se produisait avant les modifications de l’étape Jenkins.

## Correctif limité

- En mode interactif, si l’e-mail est absent ou invalide, afficher le diagnostic et **redemander cette seule valeur** jusqu’à ce qu’une adresse valide soit saisie ou que l’administrateur annule lui-même (Ctrl+C).
- Conserver intégralement le domaine, le mode Certbot, le type de base, le bind, les autorisations réseau, la base et le choix d’accès au démon Docker.
- En mode non interactif, l’absence d’un e-mail valide reste bloquante **avant** toute commande modifiant l’hôte.
- Préciser que Certbot fonctionne avec un A direct **ou un CNAME** qui résout vers l’IPv4 du serveur. Aucune modification DNS n’est effectuée par ce correctif.
- Aucun changement des scripts Ansible, des fichiers Jenkins, des configurations Nginx, de Cloudflare, des secrets ou des données.

## Validation

Un test automatisé avec un véritable pseudo-terminal reproduit deux saisies successives (Entrée vide puis adresse valide). Des tests supplémentaires vérifient les réponses erronées, la préservation des choix de l’administrateur, le refus non interactif et le maintien des modes sans Certbot.

## Installation du patch depuis Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
unzip -o /home/raph/Downloads/AUTOKOM-GCP-JENKINS-ACME-RETRY-2026-11-07.zip -d .
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_gcp_jenkins_acme_prompt_20261107 \
  tests.test_phase2 tests.test_menu_domain_v3
git diff --check
git add scripts/lib/services.py tests/test_autokom_gcp_jenkins_acme_prompt_20261107.py \
  docs/AUTOKOM-GCP-JENKINS-EMAIL-RETRY-2026-11-07.md
git commit -m "fix: retry invalid ACME email during Jenkins installation"
git push origin main
```

## Reprise sur GCP

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
```

Pendant l’installation, **l’administrateur choisit lui-même** les mêmes options ou les adapte. S’il sélectionne le nouveau domaine `jenkins.ik0m.tech` et le mode Certbot, AUTOKOM demande une adresse e-mail ACME. Une réponse vide ne quitte plus l’installation, elle redemande l’adresse. Le nom résout actuellement via un CNAME vers `ik0m.tech`, qui résout vers l’adresse publique GCP `136.116.89.163` selon les observations de l’administrateur. Cela ne garantit pas, à lui seul, que les ports HTTP/HTTPS sont accessibles depuis Internet ; Certbot vérifiera réellement la validation ACME.

**Aucune modification du DNS ou de la configuration AWS n’est autorisée par ce patch.**
