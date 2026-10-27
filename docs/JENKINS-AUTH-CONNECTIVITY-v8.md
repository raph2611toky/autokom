# AUTOKOM — Authentification Jenkins et affichage Git (v8)

## Pourquoi la phase Jenkins pouvait échouer avec des identifiants valides

La version précédente utilisait `jenkins-cli.jar -webSocket -auth @FICHIER`.
L'authentification `-auth @FICHIER` est documentée pour le mode `-http` ;
le transport WebSocket peut donc échouer indépendamment de la validité du token.
Ce module utilise maintenant `-http` sur `http://127.0.0.1:<port>/`, directement
sur la boucle locale et sans exposer de nouveau port.

Si Jenkins CLI échoue, AUTOKOM consulte l'API REST **locale**
`/whoAmI/api/json`, via HTTP Basic avec le token uniquement en mémoire :

- Token invalide (401/403 ou identité anonyme) : jusqu'à **3 essais**.
- Token valide, CLI défaillante : message **transport Jenkins CLI** ;
  pas de nouvelle question d'identifiants.
- API REST locale inaccessible : message **réseau/service** et arrêt sans modification.
- Identité CLI reconnue mais contrôle Groovy interdit : message **Overall/Administer**.

Aucun secret n'est inclus dans les paramètres de processus ni écrit dans les
logs. Les jetons temporaires sont créés avec permissions privées et supprimés
après chaque tentative. Les redirections HTTP de l'API d'identité sont bloquées
pour que le token ne puisse pas être transmis à un autre hôte.

## Mettre à jour et retenter

Sur Kali, fusionnez le ZIP à la racine du dépôt, exécutez les tests et poussez.

Sur AWS :

```bash
sudo autokom update
sudo autokom install --check
sudo autokom install
```

La phase Jenkins déjà conforme doit être sautée. Le token est demandé dans
le terminal uniquement lorsque des plugins ou contrôles doivent être appliqués.

## Diagnostic SANS exposer le token si le problème persiste

```bash
sudo systemctl is-active jenkins
sudo ss -lntp | grep ':8080'
curl --noproxy '*' -sS -o /dev/null -w 'Jenkins HTTP %{http_code}\n' \
  http://127.0.0.1:8080/whoAmI/api/json
sudo java -jar /opt/autokom/tools/jenkins-cli.jar -s http://127.0.0.1:8080/ -http help | head -20
```

Ne pas coller de token dans une commande, une capture d'écran ou un journal.
Pour une erreur persistante de transport CLI, vérifier aussi la version du JAR
avec le Jenkins actuellement installé et `journalctl -u jenkins`.

## `autokom update`

Le loader demeure temporaire pendant `git pull --ff-only --no-stat`. À la fin,
AUTOKOM imprime le diffstat généré **par Git** depuis l'ancienne révision vers
la nouvelle (`git diff --stat --color=always`, seulement si le terminal accepte
la couleur). Les ajouts sont verts, les suppressions rouges. Avec `NO_COLOR` ou
une sortie redirigée, aucun ANSI n'est émis. Si Git n'a rien changé, il n'y a
aucun diffstat supplémentaire.
