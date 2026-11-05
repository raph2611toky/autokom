# AUTOKOM — Reprise runtime, sécurité des ports et parcours de publication

## Base du correctif

Patch préparé sur une copie du dépôt comprenant la dernière évolution `3a5071` (UX, détection Vite, suivi des stages). Il ne reprend pas les anciens patches. Application impérativement vérifiée par `git apply --check` sur Kali avant modification.

## Changements

1. `autokom runtime doctor` : contrôle lecture seule de l'état des unités systemd, de la présence du socket et surtout d'un `ping` JSON **en tant qu'utilisateur Unix `autokom`**, suivi d'une lecture authentifiée du job Jenkins. `systemctl active` seul ne suffisait pas.
2. Le broker root alloue un port hôte via `docker run --publish 127.0.0.1::<port_interne>` puis contrôle le mapping Docker inspecté (une seule publication TCP, IPv4 localhost et port valide). Plus de balayage préalable des ports, donc plus de fenêtre de collision entre le test et le démarrage. Le numéro reste une information interne pour Nginx.
3. Le healthcheck ne suit plus les redirections hors de la machine.
4. Les API **clients** n'incluent plus `internal_port`, `ci_provider` ni les noms des moteurs d'exécution. Le port de l'image Vite est fixé à 8080 **côté serveur**. Le manifeste signé du runner et le broker gardent les paramètres internes indispensables à l'exécution : ils ne sont pas des API clients.
5. Une seule action client « Déployer » exécute la sauvegarde, la validation du serveur puis l'envoi en file ; elle n'affiche pas de boîte native de confirmation. L'utilisateur ne voit plus les noms des outils internes, les messages « mode connecté » ou le bandeau de notification en haut. Les erreurs restent visibles au niveau du formulaire. Le mode démo est conservé.
6. Dans « Mes projets », les étapes ont des noms compréhensibles. Les journaux de diagnostic bruts restent accessibles aux administrateurs sur Ubuntu.

## Kali Linux — vérifier avant publication

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git log -1 --oneline

git apply --check /home/raph/Downloads/AUTOKOM_Runtime_Socket_UX_PortSecurity_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Runtime_Socket_UX_PortSecurity_2026-11-05.patch

npm --prefix platform/frontend test
PYTHONPATH=scripts/lib python3 -m unittest -v \
  tests.test_autokom_runtime_broker \
  tests.test_autokom_runtime_deployment_hardening \
  tests.test_autokom_free_static_build
git diff --check
git status --short
```

Si tous les contrôles réussissent et après avoir relu `git diff`, enregistrer les fichiers du correctif :

```bash
git add scripts/lib/autokom_platform_broker.py scripts/lib/autokom_runtime_admin.py \
  platform/backend/apps/deployments/runs \
  platform/backend/apps/projects/configurations/serializers.py \
  platform/backend/apps/projects/swagger_helpers.py \
  platform/frontend/src/pages/Deploy.jsx \
  platform/frontend/src/pages/LiveProjects.jsx \
  platform/frontend/src/services/projectWorkflow.js \
  platform/frontend/tests \
  tests/test_autokom_runtime_deployment_hardening.py \
  docs/AUTOKOM-RUNTIME-SOCKET-UX-PORTS.md

git commit -m "fix: verify runtime socket and streamline secure deployments"
git push origin main
```

## Ubuntu AWS — ne pas provoquer de lancement client avant le diagnostic

Après le commit sur Kali :

```bash
sudo autokom update
sudo autokom runtime doctor
```

Le doctor doit confirmer l'accès au socket **depuis le compte `autokom`** et la disponibilité du job Jenkins. Il ne valide pas encore la construction d'une image.

Si le doctor échoue :

```bash
sudo stat -c '%A %U:%G %n' /run/autokom /run/autokom/platform-control.sock
sudo namei -l /run/autokom/platform-control.sock
sudo journalctl -u autokom-platform-broker -u autokom-deployment-worker -n 50 --no-pager
sudo autokom runtime setup --check
```

N'envoyer aucun journal contenant code OAuth, token, adresse privée ou valeur de secret sans l'avoir expurgé.

Si `runtime setup --check` réussit, la commande AUTOKOM suivante **met à jour les unités systemd et redémarre le broker et le worker** (sans toucher à Nginx ni aux zones DNS). Elle demande une confirmation et interrompt temporairement le traitement des déploiements :

```bash
sudo autokom runtime setup --apply
sudo autokom runtime doctor
```

Si le doctor signale un job absent/inaccessible :

```bash
sudo autokom runtime jenkins --check
```

Faire la synchronisation `sudo autokom runtime jenkins --apply` uniquement après examen de cette prévisualisation et des credentials Jenkins. L'agent `autokom-isolated` et ses permissions doivent exister.

Après diagnostic réussi, déployer Django avant React :

```bash
sudo autokom app deploy --check
sudo autokom app deploy
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
```

Il n'est pas nécessaire de refaire le setup DNS, Nginx, Node.js ou Certbot.

## Recette de bout en bout

1. Ouvrir le compte AUTOKOM et choisir le projet GitHub React/Vite, dossier source contenant `package.json` et `package-lock.json`.
2. Vérifier les paramètres reconnus automatiquement, puis cliquer **une fois** sur « Déployer ». Il ne doit pas y avoir de deuxième dialogue de confirmation.
3. Le backend effectue les contrôles, crée une exécution et l'affiche dans « Mes projets ».
4. Vérifier en admin l'exécution Jenkins, l'artefact local, l'activation par le broker, le healthcheck et la publication sur un domaine géré.
5. Vérifier que le conteneur ne publie pas sur `0.0.0.0`, et que le site HTTPS fonctionne sans numéro de port côté navigateur.

Contrôles administrateur **sans valeur de secret** :

```bash
sudo autokom runtime doctor
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}'
sudo ss -lnt
sudo journalctl -u autokom-platform-broker -u autokom-deployment-worker -n 50 --no-pager
```

### Remarques de sécurité

- Docker 28+ corrige des scénarios où des ports publiés sur localhost pouvaient être accessibles depuis le même segment de réseau dans certaines configurations. Vérifier la version Docker avant une utilisation multi-clients exposée à Internet (documentation officielle Docker).
- Les sources utilisées pour fabriquer le patch ont passé les tests locaux, mais aucun vrai déploiement Docker/Jenkins n'a été exécuté sur AWS depuis cet environnement. Le `503` de production **ne peut pas être attribué définitivement** aux droits du socket sans la sortie de `runtime doctor`.
- La suppression des ports des API publiques n'empêche pas le runner signé et le broker d'utiliser leurs ports internes (nécessaire à la publication).
