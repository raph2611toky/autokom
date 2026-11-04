/** Client HTTP AUTOKOM compatible avec l'API Django `/api/`.
 *  Les jetons JWT ne sont pas persistés dans localStorage/sessionStorage.
 *  Un futur AuthProvider peut hydrater le client via setAccessToken().
 *  Les refresh tokens sont volontairement non stockés ici : un stockage
 *  sécurisé côté navigateur ou un BFF est à concevoir avant connexion réelle.
 */
const DEFAULT_BASE = '/api'
let accessToken = null

function apiBase() {
  const value = (import.meta.env?.VITE_AUTOKOM_API_BASE || DEFAULT_BASE).replace(/\/+$/, '')
  // Empêche l'envoi des jetons vers un domaine contrôlé par un tiers.
  if (value !== DEFAULT_BASE) throw new Error('La base API AUTOKOM doit être /api (proxy local).')
  return value
}

export function setAccessToken(token) {
  if (token !== null && (typeof token !== 'string' || !/^[^\s]{20,8192}$/.test(token))) {
    throw new TypeError('Jeton d’accès invalide.')
  }
  accessToken = token
}
export function clearAccessToken() { accessToken = null }

export class ApiError extends Error {
  constructor(message, status, errors) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errors = errors ?? null
  }
}

export async function apiRequest(path, { method = 'GET', body, signal, headers = {} } = {}) {
  if (typeof path !== 'string' || !/^\/[a-zA-Z0-9/_-]*(?:\?[a-zA-Z0-9_=%&.-]*)?$/.test(path) || path.startsWith('//')) {
    throw new TypeError('Chemin API invalide.')
  }
  const requestHeaders = { Accept: 'application/json', ...headers }
  if (accessToken) requestHeaders.Authorization = `Bearer ${accessToken}`
  if (body !== undefined && !(body instanceof FormData)) requestHeaders['Content-Type'] = 'application/json'
  const response = await fetch(`${apiBase()}${path}`, {
    method, headers: requestHeaders,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal,
  })
  let payload = null
  try { payload = await response.json() } catch { /* réponse sans JSON */ }
  if (!response.ok || payload?.success === false) {
    throw new ApiError(
      typeof payload?.message === 'string' && payload.message.length < 300
        ? payload.message : `Requête refusée (HTTP ${response.status}).`,
      response.status, payload?.errors,
    )
  }
  return payload?.data ?? payload
}

export const api = Object.freeze({
  get: (path, options) => apiRequest(path, { ...options, method: 'GET' }),
  post: (path, body, options) => apiRequest(path, { ...options, method: 'POST', body }),
  patch: (path, body, options) => apiRequest(path, { ...options, method: 'PATCH', body }),
  delete: (path, options) => apiRequest(path, { ...options, method: 'DELETE' }),
})
