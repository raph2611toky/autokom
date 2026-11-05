// Compatibilité pour les services d'analyse existants. La connexion réelle
// Le mode GitHub réel utilise la GitHub App Django; le mode statique conserve ses démos.
import { API_AVAILABLE, GITHUB_AVAILABLE } from './runtime.js'
export const platformBackendEnabled = API_AVAILABLE
export const githubBackendEnabled = GITHUB_AVAILABLE
