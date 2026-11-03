/**
 * AUTOKOM peut être développé sans API. Cette option n'émule jamais un compte,
 * un dépôt GitHub, une analyse, un pipeline ou un déploiement.
 * La passer à false lorsque le backend est effectivement disponible.
 */
export const FRONTEND_ONLY = import.meta.env?.VITE_AUTOKOM_FRONTEND_ONLY !== 'false'
export const API_AVAILABLE = !FRONTEND_ONLY && import.meta.env?.VITE_AUTOKOM_API_ENABLED !== 'false'
export const GITHUB_AVAILABLE = API_AVAILABLE && import.meta.env?.VITE_AUTOKOM_GITHUB_ENABLED !== 'false'
