import { api, cookieAuthEnabled, clearAccessToken, setAccessToken } from './api.js'

/** Adaptateur aux routes réellement implémentées dans Django.
 * N'exécute aucune requête dans les modules importés : utilisable en mode démonstration.
 */
export const authApi = Object.freeze({
  register: ({ fullName, email, password, passwordConfirm }) => api.post('/auth/register/', {
    full_name: fullName, email, password, password_confirm: passwordConfirm,
  }),
  login: async ({ email, password }) => {
    if (cookieAuthEnabled) {
      const result = await api.post('/auth/browser/login/', { email, password })
      return { user: result.user, accessExpiresIn: result.access_expires_in ?? null }
    }
    const result = await api.post('/auth/login/', { email, password })
    if (!result?.tokens?.access) throw new Error('Réponse d’authentification incomplète.')
    setAccessToken(result.tokens.access)
    return { user: result.user, accessExpiresIn: result.tokens.expires_in ?? null }
  },
  me: () => api.get('/auth/me/'),
  logout: async () => {
    if (cookieAuthEnabled) return api.post('/auth/browser/logout/', {})
    // Ancien mode Bearer : une révocation globale est possible sans token de refresh.
    try { return await api.post('/auth/logout-all/', {}) } finally { clearAccessToken() }
  },
  logoutAll: async () => {
    try { return await api.post('/auth/logout-all/', {}) }
    finally {
      if (cookieAuthEnabled) await api.post('/auth/browser/logout/', {})
      else clearAccessToken()
    }
  },
  verifyEmail: token => api.post('/auth/email/verify/', { token }),
  resendEmail: email => api.post('/auth/email/resend/', { email }),
  forgotPassword: email => api.post('/auth/password/forgot/', { email }),
  resetPassword: ({ token, newPassword, confirm }) => api.post('/auth/password/reset/', {
    token, new_password: newPassword, new_password_confirm: confirm,
  }),
  permissions: () => api.get('/auth/me/permissions/'),
  sessions: () => api.get('/auth/sessions/'),
  revokeSession: id => {
    if (!/^[0-9a-f-]{36}$/i.test(id)) throw new TypeError('Identifiant de session invalide.')
    return api.delete(`/auth/sessions/${id}/`)
  },
  // GitHub App - parcours OAuth réel désactivé tant que l'UI callback n'est pas raccordée.
  githubStart: intent => api.get(`/auth/github/start/?intent=${intent === 'signup' ? 'signup' : 'login'}`),
  githubExchange: ({ ticket, browserBinding }) => cookieAuthEnabled
    ? api.post('/auth/browser/github/exchange/', { ticket, browser_binding: browserBinding })
    : api.post('/auth/github/exchange/', { ticket, browser_binding: browserBinding }),
})
