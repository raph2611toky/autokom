# Correctif AUTOKOM — publication automatique sur réseau Docker interne

**Base vérifiée :** dépôt public `raph2611toky/autokom`, branche `main`, code de `scripts/lib/autokom_platform_broker.py` consulté le 6 novembre 2026. Le paquet **ne modifie pas** les serveurs lors de sa création ; c'est une proposition de code à appliquer et tester dans Kali avant tout déploiement de production.

## Problème

La procédure actuelle démarre l'application cliente sur un réseau `docker network create --internal` et demande simultanément `docker run --publish 127.0.0.1::<port>`. Sur certaines versions de Docker 29, l'inspection du port retourne une liste vide. Le broker refuse alors `runtime.deploy`, supprime le conteneur, tandis que l'image importée reste présente.

Le cas utilisateur prouve : Docker actif, archive BuildKit (30,7 Mo) présente, image `autokom-local:<run_id>` chargée, conteneur absent, et `BrokerError` à `runtime.deploy`. L'incompatibilité de mapping est une hypothèse hautement plausible, pas une trace Docker suffisante pour prouver l'étape précise.

## Architecture corrigée

```
Internet -> Nginx domaine HTTPS
          -> 127.0.0.1:port_choisi (Nginx ingress privé)
          -> IP Docker interne:8080 (réseau AUTOKOM --internal)
          -> application cliente sans port Docker publié
```

Le broker gère **tout** le cycle de vie : préflight, création du relais Nginx, vérification de l'adresse Docker par inspection, retour de port 127.0.0.1 existant, healthcheck, rollback, nettoyage et suppression du relais après remplacement de la version précédente. Un watchdog contrôle l'IP Docker d'un relais existant au redémarrage et toutes les 90 secondes, en refusant une configuration Nginx manquante ou altérée.

## Installation dans Kali

```bash
cd /home/raph/SOUTENANCE/AUTOKOM

# IMPORTANT : vérifier l'absence de modifications locales non sauvegardées
git status --short

unzip -o ~/Downloads/AUTOKOM-INGRESS-AUTOMATIQUE-2026-11-06.zip \
  -d ~/Downloads/AUTOKOM-INGRESS-AUTOMATIQUE-2026-11-06

python3 ~/Downloads/AUTOKOM-INGRESS-AUTOMATIQUE-2026-11-06/INSTALLER.py \
  /home/raph/SOUTENANCE/AUTOKOM --check

python3 ~/Downloads/AUTOKOM-INGRESS-AUTOMATIQUE-2026-11-06/INSTALLER.py \
  /home/raph/SOUTENANCE/AUTOKOM --apply

python3 -m compileall -q scripts/lib
PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_internal_ingress
PYTHONPATH=scripts/lib python3 -m unittest discover -s tests -p 'test_*.py'
git diff --check
git diff --stat
git status --short
```

**IMPORTANT :** Les tests existants qui supposent encore `docker run --publish` devront être adaptés au nouveau contrat `ingress_mode=nginx-internal-v1`, si leur comportement attendu est historiquement dépendant de Docker. Ne pas ignorer un test rouge. Les 14 tests fournis sont des tests **hermétiques** ; ils ne prouvent pas que Nginx + Docker + Django fonctionnent ensemble sur le serveur.

## Publication de la modification

Après revue des diffs et réussite de la suite de tests :

```bash
git add scripts/lib/autokom_platform_broker.py \
  scripts/lib/autokom_runtime_admin.py \
  scripts/lib/autokom_internal_ingress.py \
  tests/test_autokom_internal_ingress.py
git commit -m 'fix: automatic secure ingress for Docker internal deployments'
git push origin main
```

Puis, sur Ubuntu, la procédure de **mise à jour de la plateforme** (une fois pour la nouvelle version) :

```bash
sudo autokom update
sudo autokom runtime setup --check
sudo autokom runtime setup --apply
sudo autokom runtime doctor
```

La commande `runtime setup --apply` doit être lancée en fenêtre de maintenance (confirmation interactive `INSTALLER`) : elle applique le rôle Ansible géré par AUTOKOM et redémarre les services concernés. Ne pas exécuter si des publications sont actives. Pour les publications suivantes, l'ingress est géré automatiquement par le broker, sans manipulation Docker/Nginx sur l'hôte.

Le `autokom update` seul ne redémarre pas le broker. Le rôle Ansible `ansible/roles/platform_runtime/tasks/main.yml` redémarre `autokom-platform-broker` et `autokom-deployment-worker` lors de l'application du runtime. Vérifier la version active et les démarrages dans systemd avant toute nouvelle publication. Ne jamais redémarrer pendant une publication en cours.

```bash
sudo systemctl show autokom-platform-broker.service \
  -p ActiveState -p ExecMainStartTimestamp
sudo journalctl -u autokom-platform-broker.service -n 40 --no-pager
```

**Ne pas** lancer manuellement `docker run`, `docker network connect`, créer de règle NAT, modifier le pare-feu ou changer `--internal`. AUTOKOM doit provisionner toutes ses propres ressources.

## Démonstration / validation nécessaire sur l'hôte Ubuntu

À effectuer sur un nouveau déploiement de test avant bascule d'un site de production :

1. BuildKit OCI RUN (déjà validé) et Jenkins `SUCCESS` ; image importée et archive cohérente.
2. Docker `inspect` : un seul réseau `Internal=true`, aucun `HostPort` de conteneur publié ; port applicatif interne cohérent (8080 dans le cas présent).
3. Nginx : relais lié exclusivement à `127.0.0.1`, test de sonde interne `204`, réponse `/` conforme et site HTTPS accessible.
4. Échec santé simulé : nouvelle publication retirée ; ancienne publication et domaine conservés.
5. Publication suivante réussie : commit supprime l'ancien conteneur et son relais, sans modifier les domaines des autres clients.
6. Restart Nginx et Docker : reprise du relais, changement d'IP détecté automatiquement si la configuration approuvée n'a pas été altérée.
7. Erreur de permissions, disque insuffisant, absence Nginx et réseau multi-attachement : refus fermé avec code d'erreur sans fuite d'identifiants.
8. Vérifier WebSocket / SSE et en-têtes proxy si l'application cliente en dépend. Les règles du proxy public peuvent nécessiter une évolution distincte.

## Limites et sécurité

- Ce correctif ne peut pas rendre toutes les erreurs futures impossibles. Il automatise les cas prévisibles et échoue de manière contrôlée pour les anomalies de sécurité.
- Les vérifications d'intégration n'ont pas été effectuées sur l'infrastructure de production : aucun accès SSH, Docker ni Jenkins à cette instance depuis ce paquet.
- Le conteneur ne gagne pas d'accès sortant par défaut. Le serveur hôte (Nginx) accède au sous-réseau Docker interne : vérifier les règles de filtrage réseau et la politique d'isolation multiclient lors du déploiement pilote.
- Ne pas mettre les secrets dans les journaux ; les nouveaux codes `AUTOKOM_*` sont fixes.
- Le code existant de rollback n'est pas entièrement transactional en cas de panne de Nginx + panne disque simultanées ; mettre la résilience de ces erreurs multi-défaillances sous tests de défaillance avant un déploiement SaaS multi-clients.
- Les pipelines qui utilisent directement le port Docker publié devront être adaptés.
