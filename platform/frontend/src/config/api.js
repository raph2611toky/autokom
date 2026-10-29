/**
 * Point unique de configuration de la future API AUTOKOM.
 * Les variables VITE_* sont PUBLIQUES : aucune clé ou secret ici.
 * L'authentification GitHub et les tokens Jenkins seront gérés par le backend.
 *
 * Plus tard : créer une instance Axios avec baseURL + timeout + cookies
 * HttpOnly/SameSite/CSRF et exposer des méthodes métier.
 */
export const apiConfig = Object.freeze({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 15000,
  withCredentials: true,
})

export const apiRoutes = Object.freeze({
  health: '/health',
  infrastructure: '/infrastructure',
  installs: '/installations',
  githubConnection: '/integrations/github',
  githubRepositories: '/integrations/github/repositories',
  pipelines: '/pipelines',
  deployments: '/deployments',
  metrics: '/observability/metrics',
  logs: '/logs',
  incidents: '/security/incidents',
})

// Le frontend livré est intentionnellement sans appels réseau.
export const BACKEND_CONNECTED = false
