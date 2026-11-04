import { api, clearAccessToken, setAccessToken } from './api.js'

/** Contrats réels du backend. Aucun appel n'est exécuté au chargement du module. */
export const authApi = Object.freeze({
  register: ({ fullName, email, password, passwordConfirm }) => api.post('/auth/register/', {
    full_name: fullName, email, password, password_confirm: passwordConfirm,
  }),
  login: async ({ email, password }) => {
    const result = await api.post('/auth/login/', { email, password })
    if (!result?.tokens?.access) throw new Error('Réponse d’authentification incomplète.')
    setAccessToken(result.tokens.access)
    // Le refresh token n'est pas stocké (session perdue après rechargement).
    return { user: result.user, accessExpiresIn: result.tokens.expires_in ?? null }
  },
  me: () => api.get('/auth/me/'),
  logoutAll: async () => { try { return await api.post('/auth/logout-all/', {}) } finally { clearAccessToken() } },
})
