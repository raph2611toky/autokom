# AUTOKOM — Enrôlement Jenkins automatisé et sécurisé

## Objectif

`sudo autokom runtime agent --apply` doit provisionner le nœud isolé d'AUTOKOM **sans** installation manuelle, sans transcription d'un secret et sans modifier les vhosts Nginx ni les autres nœuds Jenkins.

Le serveur du projet possède Jenkins en loopback à `http://127.0.0.1:8080`, accessible par le compte `AdminJenkins` déjà configuré dans `runtime.env`. Le proxy HTTPS `jenkins.ik0m.tech` n'envoie pas les en-têtes WebSocket Upgrade/Connection ; **l'agent local ne passe donc jamais par le proxy**.

## Sécurité

- Nœud **nouveau** `autokom-builder-01` et **label inchangé** `autokom-isolated` ; éviter de réutiliser le secret du nœud `autokom-isolated`, déjà publié en clair.
- Compte Unix non privilégié `autokom-builder` sans groupe `docker`, un exécuteur.
- L'API Jenkins crée uniquement le nœud AUTOKOM approuvé et refuse d'écraser un nœud existant non conforme ; aucune exécution de Groovy arbitraire.
- L'enrôlement récupère le secret depuis Jenkins local, le valide et le stocke en mode `0400`, sans valeur dans les arguments du service ni dans les journaux. Une divergence de secret exige une rotation explicite plutôt qu'un écrasement silencieux.
- `agent.jar` est téléchargé depuis le contrôleur Jenkins local, vérifié puis installé dans le répertoire privé.
- systemd lance l'agent par WebSocket loopback avec `User=autokom-builder` et un service durci. Aucune ouverture de port supplémentaire.
- L'ACL de `/var/lib/autokom` n'accorde au builder que la **traversée** du parent des artefacts ; la lecture du répertoire privé Django demeure interdite.
- L'ancien nœud Jenkins `autokom-isolated` dont le secret a été divulgué doit être **supprimé ou révoqué**, et non seulement déconnecté. La création du nouveau nœud n'efface pas automatiquement les anciens nœuds.

## Sur Kali : patch

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline
git apply --check /home/raph/Downloads/AUTOKOM_Jenkins_Agent_Autoprovision_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Jenkins_Agent_Autoprovision_2026-11-05.patch
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_jenkins_agent_autoprovision \
  tests.test_autokom_runtime_broker \
  tests.test_autokom_agent_queue_and_views
npm --prefix platform/frontend test
git diff --check
git add scripts/lib/autokom_runtime_admin.py scripts/lib/autokom_jenkins_agent.py \
  scripts/systemd/autokom-jenkins-agent.service \
  ansible/playbooks/jenkins-agent.yml ansible/roles/jenkins_agent \
  tests/test_autokom_jenkins_agent_autoprovision.py \
  docs/AUTOKOM-JENKINS-AGENT-AUTONOME-2026-11-05.md
git commit -m 'feat: provision Jenkins agent autonomously through AUTOKOM'
git push origin main
```

## Sur Ubuntu AWS : sans installation manuelle

La commande `--check` ne modifie rien. La commande `--apply` provisionne Java/Git/ACL via Ansible, le nœud Jenkins, le secret, le binaire de l'agent et son service systemd.

```bash
sudo autokom update
sudo autokom runtime agent --check
sudo autokom runtime agent --apply
# confirmer CONNECTER
sudo autokom runtime doctor
sudo systemctl status autokom-jenkins-agent --no-pager
```

L'enregistrement et le lancement sont vérifiés via l'API Jenkins avec une attente bornée à 45 secondes. En cas d'échec :

```bash
sudo journalctl -u autokom-jenkins-agent -n 60 --no-pager
sudo systemctl status autokom-jenkins-agent --no-pager
sudo autokom runtime doctor
```

Ne pas coller de secrets dans les commandes. Ne pas utiliser le fichier vide `/var/lib/autokom-jenkins-agent-v2/secret` créé lors des tentatives précédentes : le service utilise **`.agent-secret`**, créé automatiquement.

## Limitations et prochaine recette

Ce correctif automatise **l'enrôlement et la connexion**. Un agent connecté ne valide pas à lui seul le lancement de BuildKit rootless, les credentials GitHub `autokom-github-app` / runner `autokom-runner-key`, ni l'accès au registre source. Ce sont des contrôles distincts : les vérifier sur un seul projet de test avant d'ouvrir les déploiements aux clients. Conserver les protections de l'offre gratuite.

Aucun vrai déploiement Jenkins n'a été exécuté dans l'environnement de génération de ce patch. Ne pas déclarer la mise en production achevée avant `runtime doctor` OK et un build réel réussi.
