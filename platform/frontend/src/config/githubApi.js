// Compatibilité pour les services d'analyse existants. La connexion réelle
// GitHub reste désactivée tant que l'UI simulée n'utilise pas la GitHub App.
import { API_AVAILABLE, GITHUB_AVAILABLE } from './runtime.js'
export const platformBackendEnabled = API_AVAILABLE
export const githubBackendEnabled = GITHUB_AVAILABLE
