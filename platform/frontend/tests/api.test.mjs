import test from 'node:test'
import assert from 'node:assert/strict'
import { api, setAccessToken, clearAccessToken, ensureCsrf, ApiError, cookieAuthEnabled } from '../src/services/api.js'

const originalFetch = globalThis.fetch
const originalDocument = globalThis.document
function restore() {
  clearAccessToken()
  globalThis.fetch = originalFetch
  if (originalDocument === undefined) delete globalThis.document
  else globalThis.document = originalDocument
}
function json(status, data) {
  return { ok: status >= 200 && status < 300, status,
    json: async () => ({ success: status < 400, data, ...(status >= 400 ? { message: 'Erreur API' } : {}) }) }
}

test('mode navigateur cookie HttpOnly via fetch same-origin : pas de Bearer injecté', async () => {
  assert.equal(cookieAuthEnabled, true)
  globalThis.document = { cookie: 'csrftoken=csrf-test-value' }
  const calls = []
  globalThis.fetch = async (path, options) => { calls.push([path, options]); return json(200, { id: 7 }) }
  try {
    setAccessToken('x'.repeat(48))
    assert.deepEqual(await api.get('/auth/me/'), { id: 7 })
    assert.equal(calls[0][0], '/api/auth/me/')
    assert.equal(calls[0][1].headers.Authorization, undefined)
    assert.equal(calls[0][1].credentials, 'same-origin')
    await api.post('/auth/browser/login/', { email: 'test@example.org' })
    assert.equal(calls[1][1].headers['X-CSRFToken'], 'csrf-test-value')
    assert.equal(calls[1][1].headers.Authorization, undefined)
    assert.match(calls[1][1].body, /test@example.org/)
  } finally { restore() }
})

test('initialise le CSRF via endpoint backend avant un POST', async () => {
  globalThis.document = { cookie: '' }
  const calls = []
  globalThis.fetch = async (path, opts) => {
    calls.push([path, opts])
    if (path.endsWith('/csrf/')) globalThis.document.cookie = 'csrftoken=fresh-cookie'
    return json(200, {})
  }
  try {
    await api.post('/auth/browser/login/', { email: 'client@example.org', password: 'never-log-password' })
    assert.equal(calls[0][0], '/api/auth/browser/csrf/')
    assert.equal(calls[1][0], '/api/auth/browser/login/')
    assert.equal(calls[1][1].headers['X-CSRFToken'], 'fresh-cookie')
    assert.equal(calls[1][1].credentials, 'same-origin')
  } finally { restore() }
})

test('renouvelle la session une seule fois pour les GET concurrents en 401', async () => {
  globalThis.document = { cookie: 'csrftoken=csrf-test-value' }
  let refreshes = 0
  globalThis.fetch = async (path) => {
    if (path === '/api/auth/browser/refresh/') { refreshes++; await Promise.resolve(); return json(200, {}) }
    if (path === '/api/auth/me/' && refreshes === 0) return json(401, null)
    return json(200, { user: { id: 'user-1' } })
  }
  try {
    const results = await Promise.all([api.get('/auth/me/'), api.get('/auth/me/')])
    assert.equal(refreshes, 1)
    assert.equal(results[0].user.id, 'user-1')
    assert.equal(results[1].user.id, 'user-1')
  } finally { restore() }
})

test('n’envoie aucun refresh automatique pour login ou logout en erreur 401', async () => {
  globalThis.document = { cookie: 'csrftoken=csrf-test-value' }
  let total = 0
  globalThis.fetch = async () => { total++; return json(401, null) }
  try {
    await assert.rejects(() => api.post('/auth/browser/login/', {}), error => error instanceof ApiError && error.status === 401)
    assert.equal(total, 1)
  } finally { restore() }
})

test('interdit chemins absolus, URL externes et paramétrage non autorisé', async () => {
  await assert.rejects(() => api.get('//evil.example.org'), TypeError)
  await assert.rejects(() => api.get('https://evil.example.org'), TypeError)
  await assert.rejects(() => api.post('/a/../../b/', {}), TypeError)
})

test('retourne ApiError pour une erreur Django sans exposer les secrets', async () => {
  globalThis.document = { cookie: 'csrftoken=csrf-test-value' }
  globalThis.fetch = async () => ({ ok: false, status: 403, json: async () => ({ success: false, message: 'Accès refusé' }) })
  try { await assert.rejects(() => api.get('/auth/me/'), err => err instanceof ApiError && err.status === 403) }
  finally { restore() }
})
