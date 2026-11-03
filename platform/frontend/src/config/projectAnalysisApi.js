import { platformBackendEnabled } from './githubApi.js'

const BASE = (import.meta.env.VITE_AUTOKOM_API_BASE || '/api/v1').replace(/\/+$/, '')

// Le cookie de session est HttpOnly. Le CSRF Django est transmis uniquement
// au backend AUTOKOM ; jamais aux services tiers.
function csrfToken() {
  const match = document.cookie.split('; ').find(part => part.startsWith('csrftoken='))
  return match ? decodeURIComponent(match.slice(10)) : ''
}

async function post(path, body, signal) {
  if (!platformBackendEnabled) throw new Error('Activez le backend AUTOKOM pour analyser et déployer.')
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (url.origin !== window.location.origin && url.protocol !== 'https:') throw new Error('API distante non sécurisée.')
  const response = await fetch(url.toString(), {
    method: 'POST', credentials: 'include', signal, cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(body),
  })
  let data = null
  try { data = await response.json() } catch { /* Le serveur peut renvoyer une erreur vide. */ }
  if (!response.ok) throw new Error(
    response.status === 401 ? 'Connectez-vous à votre compte AUTOKOM.' :
    response.status === 403 ? 'Accès refusé ou session CSRF expirée.' :
    response.status === 409 ? 'Une configuration ou une ressource est devenue indisponible.' :
    (typeof data?.message === 'string' ? data.message : 'Le serveur ne peut pas traiter cette demande.'),
  )
  return data
}

export function analyzeRepository({ githubRepositoryId, branch, workdir }, signal) {
  return post('/projects/analyze-source/', {
    repository_id: Number(githubRepositoryId), branch, workdir: workdir || './',
  }, signal)
}

export async function pollAnalysis(id, signal) {
  if (!/^[a-zA-Z0-9_-]{1,120}$/.test(String(id))) throw new Error('Identifiant d’analyse invalide.')
  const response = await fetch(new URL(`${BASE}/projects/analyses/${id}/`, window.location.origin), {
    method: 'GET', credentials: 'include', signal, cache: 'no-store', headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error('Le suivi de l’analyse est temporairement indisponible.')
  return response.json()
}

export function availableHostingOffers(signal) {
  if (!platformBackendEnabled) return Promise.resolve(null)
  return fetch(new URL(`${BASE}/hosting/offers/`, window.location.origin), {
    method: 'GET', credentials: 'include', signal, headers: { Accept: 'application/json' }, cache: 'no-store',
  }).then(response => response.ok ? response.json() : null)
}

export function submitDeployment(configuration) {
  // Contrat atomique futur : un import + la création d'une opération idempotente.
  const idempotencyKey = globalThis.crypto?.randomUUID?.()
  if (!idempotencyKey) throw new Error('Le navigateur ne permet pas de sécuriser cette demande.')
  return post('/projects/import-and-deploy/', { ...configuration, idempotency_key: idempotencyKey })
}
