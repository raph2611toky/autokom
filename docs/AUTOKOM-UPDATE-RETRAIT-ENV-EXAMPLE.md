# AUTOKOM Core — Mise à jour après retrait du modèle `.env.example`

## Contexte

Le commit `a8a9d76` retire notamment `platform/backend/.env.example` et
`scripts/dev/platform-runtime.env.example`, conformément au stockage de tous
les secrets dans `/etc/autokom/secrets/`.

La protection de mise à jour refusait toute suppression dans `scripts/`, même
celle du modèle obsolète et sans secret. Le nouveau garde-fou conserve :

- Validation de l'origine du dépôt, de la branche, de la lignée Git et du fast-forward.
- Présence des composants `REQUIRED_CORE` dans la version cible.
- Refus des suppressions de scripts, modules et rôles Core/Ansible.
- Exception **exacte** pour `scripts/dev/platform-runtime.env.example` uniquement.

Aucun fichier `.env` ou `.env.example` n'est restauré dans le dépôt.

## Récupération d'une version déjà bloquée

L'ancien `autokom update` a réussi le fetch avant le refus : `FETCH_HEAD` peut
encore contenir le commit `a8a9d76`. Un rattrapage d'urgence doit vérifier
`git status`, la branche `main`, `FETCH_HEAD`, `merge-base`, les suppressions
attendues et les fichiers Core obligatoires avant le `git merge --ff-only`.

Ne pas utiliser `git reset --hard`, `git checkout -f` ou supprimer le garde-fou.

Après le rattrapage, publier ce correctif sur `main`, puis relancer
`sudo autokom update` pour installer la correction permanente.
