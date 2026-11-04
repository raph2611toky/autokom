/** API REST Django via le seul reverse proxy Nginx de même origine (/api).
 * Mode cookie (par défaut en production): JWT HttpOnly, CSRF double-submit.
 * Mode bearer conservé pour les intégrations existantes (token en mémoire).
 * Aucun secret dans localStorage, sessionStorage ni variable VITE_*.
 */
const BASE = '/api'
const AUTH_MODE = import.meta.env?.VITE_AUTOKOM_AUTH_MODE || 'cookie'
if (!['cookie', 'bearer'].includes(AUTH_MODE)) throw new Error('Mode authentification invalide.')
let accessToken = null
let refreshPromise = null
let csrfPromise = null

export const cookieAuthEnabled = AUTH_MODE === 'cookie'
export class ApiError extends Error {
  constructor(message, status, errors) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errors = errors ?? null
  }
}
export function setAccessToken(token) {
  if (token !== null && (typeof token !== 'string' || !/^[^\s]{20,8192}$/.test(token))) {
    throw new TypeError('Jeton d’accès invalide.')
  }
  accessToken = token
}
export function clearAccessToken() { accessToken = null }

function validatePath(path) {
  if (typeof path !== 'string' || !/^\/[a-zA-Z0-9/_-]*(?:\?[a-zA-Z0-9_=%&.-]*)?$/.test(path) || path.startsWith('//')) {
    throw new TypeError('Chemin API invalide.')
  }
  if ((import.meta.env?.VITE_AUTOKOM_API_BASE || '/api').replace(/\/+$/, '') !== BASE) {
    throw new Error('La base API AUTOKOM doit être /api.')
  }
}
function csrfCookie() {
  if (typeof document === 'undefined') return ''
  const piece = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith('csrftoken='))
  return piece ? decodeURIComponent(piece.slice('csrftoken='.length)) : ''
}
/** Récupère le cookie CSRF par le backend, jamais un JWT. */
export async function ensureCsrf() {
  if (!cookieAuthEnabled) return
  if (csrfCookie()) return
  if (!csrfPromise) {
    csrfPromise = fetch(`${BASE}/auth/browser/csrf/`, {
      method: 'GET', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      headers: { Accept: 'application/json' },
    }).then(response => {
      if (!response.ok) throw new ApiError('Initialisation CSRF impossible.', response.status)
      if (!csrfCookie()) throw new ApiError('Cookie CSRF absent. Vérifier le proxy et HTTPS.', 403)
    }).finally(() => { csrfPromise = null })
  }
  return csrfPromise
}
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS'])
const NO_RETRY = /^\/auth\/(?:browser\/(?:csrf|login|logout|refresh|github\/exchange)|login|token\/refresh|logout|register)\//

async function execute(path, { method = 'GET', body, signal, headers = {} } = {}) {
  validatePath(path)
  method = method.toUpperCase()
  if (!/^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$/.test(method)) throw new TypeError('Méthode invalide.')
  if (cookieAuthEnabled && !SAFE.has(method)) await ensureCsrf()
  const requestHeaders = { Accept: 'application/json', ...headers }
  if (cookieAuthEnabled) {
    // La session du navigateur est exclusivement transportée par le cookie.
    delete requestHeaders.Authorization
    if (!SAFE.has(method)) requestHeaders['X-CSRFToken'] = csrfCookie()
  } else if (accessToken) requestHeaders.Authorization = `Bearer ${accessToken}`
  if (body !== undefined && !(body instanceof FormData)) requestHeaders['Content-Type'] = 'application/json'
  const response = await fetch(`${BASE}${path}`, {
    method, headers: requestHeaders,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal,
  })
  let payload = null
  try { payload = await response.json() } catch { /* erreurs sans corps JSON */ }
  if (!response.ok || payload?.success === false) {
    throw new ApiError(
      typeof payload?.message === 'string' && payload.message.length < 300
        ? payload.message : `Requête refusée (HTTP ${response.status}).`,
      response.status, payload?.errors,
    )
  }
  return payload?.data ?? payload
}

/** Une seule rotation du refresh par onglet pour les appels concurrents. */
async function refreshSession() {
  if (!refreshPromise) {
    const rotate = async () => {
      // Web Locks (lorsque disponible) coordonne les refresh entre plusieurs onglets.
      if (typeof navigator !== 'undefined' && navigator.locks?.request) {
        return navigator.locks.request('autokom-browser-refresh', async () => {
          // Un autre onglet a peut-être renouvelé le cookie avant d'obtenir le verrou.
          try { return await execute('/auth/me/') }
          catch (error) { if (!(error instanceof ApiError) || error.status !== 401) throw error }
          return execute('/auth/browser/refresh/', { method: 'POST' })
        })
      }
      return execute('/auth/browser/refresh/', { method: 'POST' })
    }
    refreshPromise = rotate().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}
export async function apiRequest(path, options = {}) {
  try { return await execute(path, options) }
  catch (error) {
    if (!cookieAuthEnabled || !(error instanceof ApiError) || error.status !== 401 ||
        NO_RETRY.test(path) || options.retry === false || options.signal?.aborted) throw error
    try { await refreshSession() } catch { throw error }
    return execute(path, options)
  }
}
export const api = Object.freeze({
  get: (path, options) => apiRequest(path, { ...options, method: 'GET' }),
  post: (path, body, options) => apiRequest(path, { ...options, method: 'POST', body }),
  put: (path, body, options) => apiRequest(path, { ...options, method: 'PUT', body }),
  patch: (path, body, options) => apiRequest(path, { ...options, method: 'PATCH', body }),
  delete: (path, options) => apiRequest(path, { ...options, method: 'DELETE' }),
})
