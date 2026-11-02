# AUTOKOM — Frontend (interface uniquement)

React + Vite + CSS pur. Pages : **Connexion**, **Configurer un projet**, **Sites & domaines**.
Aucun appel HTTP, aucun endpoint prédéfini, aucune authentification fictive et aucune découverte simulée.

## Installation

```bash
cd /home/raph/SOUTENANCE/AUTOKOM/platform
mv frontend frontend.backup
unzip /chemin/vers/autokom_frontend_v4_sans_api.zip -d .
cd frontend
npm install
npm run dev
```

Naviguer vers :

- `http://localhost:5173/` : interface de connexion minimaliste (aussi `/login`).
- `http://localhost:5173/deploy` : configuration de projet (UI uniquement).
- `http://localhost:5173/sites` : inventaire, état vide.

**Important** : il n'existe aucune authentification ni protection de route avant le backend. Le bouton « Se connecter » ne vérifie pas les identifiants, n'envoie aucune requête, n'enregistre aucun mot de passe et n'ouvre aucune session ; il affiche simplement que la connexion n'est pas encore disponible. Pour visualiser les pages, ouvrir directement `/deploy` ou `/sites` durant le développement. **Ne pas exposer le frontend comme une console d'administration sécurisée avant l'intégration réelle.**

## Déploiement

Assistant sur 4 étapes : source & outil CI/CD configurables, construction, publication, automatisation/sécurité. Fournisseur GitHub proposé par défaut **sans connexion factice**. L'utilisateur peut saisir manuellement une URL Git. « Se connecter » au fournisseur affiche simplement un message de disponibilité future.

Les brouillons de configuration (sans valeurs secrètes) sont stockés dans `localStorage` et exportables en JSON. Ils ne déclenchent ni webhook, ni pipeline, ni déploiement.

## Sites & domaines

Aucun site fabriqué, aucune statistique artificielle. Inventaire vide avec statuts « — ». Le bouton de découverte reste désactivé jusqu'à l'intégration réelle.

## Organisation

```text
frontend/
├── public/autokom-logo.png
├── src/
│   ├── components/Header.jsx
│   ├── config/api.js              # fichier de réserve, sans API ni endpoint
│   ├── pages/{Login,Deploy,Sites}.jsx
│   ├── styles/main.css
│   ├── App.jsx
│   └── main.jsx
├── index.html
├── vite.config.js
└── package.json
```

Pour la production, les permissions, la gestion des identifiants, les sessions et les actions d'infrastructure devront être réalisées et vérifiées côté serveur.
