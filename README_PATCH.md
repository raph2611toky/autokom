# AUTOKOM — Correctif : dépôt privé + déploiement autonome du backend

Ce ZIP est un **correctif incrémental**, pas un remplacement complet du dépôt.

1. Depuis le dépôt AUTOKOM, récupérer l'archive puis l'extraire dans un répertoire temporaire.
2. Prévisualiser les modifications :

   ```bash
   python3 /chemin/du/patch/appliquer_patch.py --repo /home/raph/SOUTENANCE/AUTOKOM
   ```

3. Appliquer :

   ```bash
   python3 /chemin/du/patch/appliquer_patch.py --repo /home/raph/SOUTENANCE/AUTOKOM --apply
   ```

4. Examiner `git diff --stat`, puis lire `docs/AUTOKOM-PLATFORM-SERVICE-PRIVATE-UPDATE.md`.
5. Exécuter les tests de base :

   ```bash
   python3 -m unittest discover -s /chemin/du/patch/tests -p 'test_*.py' -v
   autokom app --help
   sudo autokom update --check
   ```

L'application du patch ne supprime pas de fichiers, ne remplace pas le frontend
et ne modifie aucune base MySQL ni service système avant l'exécution explicite
par l'administrateur des nouvelles commandes `autokom app setup` et `autokom app deploy`.

**Important** : l'archive fournie antérieurement de `autokom.zip` est la version
publique disponible dans la conversation, et nous n'avons pas d'accès direct à
la dernière branche privée. Le patcher vérifie la structure de code avant de
modifier un fichier et refuse les versions divergentes, au lieu d'écraser le
code récent sans contrôle. Si `scripts/lib/autokom_cli.py` est manquant après
le commit de refonte, restaurer d'abord les scripts AUTOKOM Core.
