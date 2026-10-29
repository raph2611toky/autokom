# AUTOKOM — Frontend React

Première version **frontend uniquement** d'AUTOKOM : React, Vite, React Router, Lucide Icons, CSS natif. Pas de Tailwind, pas de Bootstrap, pas de composant UI généré et pas de backend fictif.

## 1. Lancer le projet

Prérequis : Node.js 20.19+ ou 22.12+ et npm.

Depuis le répertoire du dépôt AUTOKOM :

```bash
cd platform/frontend
npm install
cp .env.example .env
npm run dev
```

Puis accéder à `http://localhost:5173/`.

Production :

```bash
npm run build
npm run preview
```

## 2. Arborescence

```text
frontend/
├── public/
│   ├── autokom-logo.png     # Icone A/K récupérée du projet initial
│   └── favicon.png
├── src/
│   ├── components/
│   │   ├── Sidebar.jsx
│   │   └── Navbar.jsx
│   ├── pages/
│   │   ├── Dashboard.jsx
│   │   ├── Infrastructure.jsx
│   │   ├── Installation.jsx
│   │   ├── Pipelines.jsx
│   │   ├── Deployments.jsx
│   │   ├── Observability.jsx
│   │   ├── Logs.jsx
│   │   ├── CyberResilience.jsx
│   │   └── Settings.jsx
│   ├── styles/
│   │   ├── main.css
│   │   └── pages.css
│   ├── config/
│   │   └── api.js
│   ├── App.jsx
│   └── main.jsx
├── index.html
├── vite.config.js
├── .env.example
├── .gitignore
└── package.json
```

**Deux composants transversaux seulement**, la navigation et l'entête. Les écrans restent isolés dans leurs pages ; ils gèrent localement leurs états. L'AppShell/routeur vit dans App.jsx. Les variations de cartes et boutons reposent sur du CSS, pas un « générateur » de composants.

## 3. Pages

- `/dashboard` : tableau de bord et composants connus sans chiffres inventés
- `/infrastructure` : groupes de services et emplacement de la découverte réelle
- `/installation` : sélection UI des modules, aucune commande shell exécutée
- `/pipelines` : écran de connexion GitHub App et dépôts en attente de synchronisation
- `/deployments` : assistant projet → build → sécurité → récapitulatif, brouillon non sensible stocké localement
- `/observability` : tableaux de mesures vides en attendant Prometheus, raccourcis vers outils
- `/logs` : interface de recherche Loki/OpenSearch, sans événements simulés
- `/cyber-resilience` : flux cible et playbooks **non exécutables**
- `/settings` : configuration publique API et préférence d'affichage compacte

## 4. Gestion des secrets et future API

Le frontend n'authentifie pas directement Jenkins ni GitHub. Il **ne doit jamais** recevoir de GitHub App private key, token de gestion ou secret Jenkins. Les variables `VITE_*` sont injectées dans le bundle public ; seules des valeurs publiques peuvent y figurer.

Futur backend conseillé :

1. Authentification AUTOKOM, sessions HttpOnly/SameSite, autorisations par rôle, CSRF et audit.
2. GitHub App : OAuth/installation et sélection de dépôts autorisés, credentials chiffrés côté serveur.
3. Backend GitHub : configuration webhooks à l'inscription des projets, validation des signatures `X-Hub-Signature-256`, idempotence et gestion des retries.
4. Backend Jenkins : génération de Jenkinsfile selon stack/politique, credentials serveur, triggering contrôlé, récupération des statuts et logs.
5. État réel des outils/serveurs depuis AUTOKOM CLI ; métriques Prometheus ; logs Loki/OpenSearch ; alertes/événements du moteur de sécurité.
6. Actions cyber-résilience soumises à politiques, approbations et journal d'audit. Ne jamais exposer une exécution shell arbitraire à l'interface.

`src/config/api.js` centralise la baseURL et les futures routes ; l'instance Axios sera ajoutée **quand l'API sera disponible**. Aucun appel réseau fictif aujourd'hui.

## 5. Portée et limites

Cette version est un prototype visuel fonctionnel côté navigateur. Les boutons de connexion et d'exploitation affichent clairement qu'une API est requise ; ils **n'installent**, **ne déploient** et **n'exécutent** rien.

Les composants apparaissent comme outils ciblés ou précédemment travaillés ; leur version, installation et état courant doivent être découverts, jamais supposés.

Les brouillons locaux ne contiennent que les réglages de build, branche, dépôt et interrupteurs de politique : **aucune clé, mot de passe ou variable sensible**.
