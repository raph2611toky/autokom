# Installation sans duplication et sans backup

Depuis `/home/raph/SOUTENANCE/AUTOKOM/platform` :

```bash
python3 /chemin/vers/scripts/nettoyer_et_installer.py \
  --zip /home/raph/Downloads/AUTOKOM_Platform_Backend_Clients_Plans_Models_V2.zip \
  --backend /home/raph/SOUTENANCE/AUTOKOM/platform/backend
```

Le premier lancement ne fait qu'annoncer les modifications. Si le dossier et le zip sont corrects, répéter en ajoutant `--apply`.

**ATTENTION :** l'option `--apply` supprime sans sauvegarde les anciens dossiers de code `apps`, `config`, `helpers`, `templates`, `statics`, `tests` et les fichiers gérés. Elle conserve `.env`, `media`, `.venv` et `staticfiles`, et ne touche pas MySQL. Si des migrations historiques numérotées existent, le script refuse l'opération pour éviter de casser l'historique SQL. Ne pas utiliser contre une base MySQL déjà peuplée sans stratégie de migration.
