# AUTOKOM — Logo et réglages légers des écrans Auth

Ce correctif **ne touche pas aux services de production**, au runtime Django, aux classes des autres pages, ni aux variables d'environnement. Il s'applique au frontend déjà publié via la commande `autokom app frontend deploy`.

## Livrable

- `platform/frontend/public/autokom-logo.png` contient **l'image PNG fournie** par le responsable du projet (512 × 488 pixels, fond transparent). Vite la recopiera à la racine de `dist/`. Les références préexistantes dans Landing, Header et AuthFrame n'ont donc pas besoin d'être remplacées.
- Le favicon du navigateur utilise désormais le même logo.
- Le composant `src/components/GitHubMark.jsx` affiche la silhouette GitHub pleine, `fill=currentColor`; les styles utilisent les couleurs existantes du thème clair/sombre. Il remplace les logos GitHub en contour du sélecteur de dépôts et est utilisé sur les écrans d'authentification.
- Connexion : le bouton « Mot de passe oublié ? » passe immédiatement sous le champ mot de passe (mode API comme avant). Le bouton GitHub passe en toute dernière action, après « Créer un compte ». « Configurer un projet » disparaît de cette page.
- Inscription : dernière action « S’inscrire avec GitHub » à la place de « Configurer un projet ».
- Les messages de fonctionnalité non activée et le mode présentation sont conservés : **l'authentification OAuth GitHub réelle n'est pas activée par ce patch**. Ne pas activer `VITE_AUTOKOM_GITHUB_ENABLED` sans raccorder et tester le flux de retour OAuth.
- Les 9 stylesheets existants, la page d'accueil animée et les modes DevOps / DevSecOps restent inchangés, hormis les quelques règles CSS des liens d'authentification.

## Kali — intégration

```bash
cd /home/raph/SOUTENANCE/AUTOKOM
git status --short
git apply --check /home/raph/Downloads/AUTOKOM_Logo_GitHub_Auth_UI_2026-11-05.patch
git apply /home/raph/Downloads/AUTOKOM_Logo_GitHub_Auth_UI_2026-11-05.patch
npm --prefix platform/frontend test
# Si les dépendances sont déjà disponibles localement :
npm --prefix platform/frontend run build
git diff --check
git add platform/frontend docs/AUTOKOM-LOGO-AUTH-UI-2026-11-05.md
git commit -m "fix(frontend): restore AUTOKOM logo and refine GitHub auth actions"
git push origin main
```

## Ubuntu — publication via AUTOKOM, sans installation manuelle

```bash
sudo autokom update
sudo autokom app frontend deploy --check
sudo autokom app frontend deploy --apply
sudo autokom app frontend status
curl -I https://auto.ikom.mg/autokom-logo.png
```

Puis ouvrir `https://auto.ikom.mg/`, `/login` et `/register` en thème clair et sombre. Tester le lien « Mot de passe oublié ? ». Après un déploiement, si l'ancien logo est encore en cache, faire un rafraîchissement forcé du navigateur.

Si nécessaire, le rollback demeure : `sudo autokom app frontend rollback --check` puis `--apply`.
