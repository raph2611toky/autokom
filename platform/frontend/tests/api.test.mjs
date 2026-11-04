import test from 'node:test'
import assert from 'node:assert/strict'
import { api, setAccessToken, clearAccessToken, ApiError } from '../src/services/api.js'

test('l’API Django est sollicitée en relatif, avec des jetons uniquement en mémoire', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (path, options) => {
    calls.push([path, options])
    return { ok: true, status: 200, json: async () => ({ success: true, data: { id: 7 } }) }
  }
  try {
    setAccessToken('x'.repeat(48))
    assert.deepEqual(await api.get('/auth/me/'), { id: 7 })
    assert.equal(calls[0][0], '/api/auth/me/')
    assert.equal(calls[0][1].headers.Authorization, `Bearer ${'x'.repeat(48)}`)
    assert.equal(calls[0][1].credentials, 'same-origin')
    clearAccessToken()
    await api.post('/auth/login/', { email: 'test@example.org' })
    assert.equal(calls[1][1].headers.Authorization, undefined)
    assert.match(calls[1][1].body, /test@example.org/)
  } finally { clearAccessToken(); globalThis.fetch = original }
})

test('le client bloque les URLs absolues et les chemins externes', async () => {
  await assert.rejects(() => api.get('//evil.example.org'), TypeError)
  await assert.rejects(() => api.get('https://evil.example.org'), TypeError)
})

test('les erreurs backend sont structurées, sans exposition de credentials', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false, status: 403, json: async () => ({ success: false, message: 'Accès refusé' }) })
  try { await assert.rejects(() => api.get('/auth/me/'), err => err instanceof ApiError && err.status === 403) }
  finally { globalThis.fetch = original }
})
