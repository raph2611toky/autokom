import test from 'node:test'
import assert from 'node:assert/strict'
import { startGitHubAccountAuth, consumeGitHubAccountCallback,
  GITHUB_BROWSER_BINDING_KEY, validatedGitHubAuthorizationUrl } from '../src/services/githubAuthFlow.js'

function storage() {
  const entries = new Map()
  return { getItem: key => entries.get(key) || null, setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key) }
}
test('connexion GitHub : redirige seulement vers le vrai OAuth et stocke le binding hors de l’URL', async () => {
  const store = storage()
  let redirected = ''
  const binding = 'b'.repeat(50)
  await startGitHubAccountAuth('login', { start: async intent => {
    assert.equal(intent, 'login')
    return { authorization_url: 'https://github.com/login/oauth/authorize?state=state-demo', browser_binding: binding }
  }, storage: store, redirect: href => { redirected = href } })
  assert.ok(redirected.startsWith('https://github.com/login/oauth/authorize?'))
  assert.ok(!redirected.includes(binding))
  assert.equal(store.getItem(GITHUB_BROWSER_BINDING_KEY), binding)
})
test('inscription GitHub transmet signup et consomme le binding une seule fois', async () => {
  const store = storage()
  await startGitHubAccountAuth('signup', { start: async intent => {
    assert.equal(intent, 'signup')
    return { authorization_url: 'https://github.com/login/oauth/authorize', browser_binding: 'c'.repeat(50) }
  }, storage: store, redirect: () => {} })
  assert.deepEqual(consumeGitHubAccountCallback('#status=created&ticket=temporary-ticket', store),
    { status: 'created', ticket: 'temporary-ticket', browserBinding: 'c'.repeat(50) })
  assert.equal(store.getItem(GITHUB_BROWSER_BINDING_KEY), null)
  assert.equal(consumeGitHubAccountCallback('#status=created&ticket=temporary-ticket', store).status, 'invalid')
})
test('destinations externes refusées, y compris sous-domaines et chemins trompeurs', () => {
  for (const url of ['https://github.com.evil.invalid/login/oauth/authorize',
    'http://github.com/login/oauth/authorize', 'https://github.com/evil',
    'https://evil.invalid/login/oauth/authorize']) {
    assert.throws(() => validatedGitHubAuthorizationUrl(url))
  }
})
test('rejet GitHub et erreur technique sont distincts sans exposer de ticket', () => {
  const store = storage()
  assert.equal(consumeGitHubAccountCallback('#status=denied', store).status, 'denied')
  assert.equal(consumeGitHubAccountCallback('#status=account_exists', store).status, 'account_exists')
  assert.equal(consumeGitHubAccountCallback('#status=some-unknown-status', store).status, 'error')
})

test('liaison GitHub d’un compte existant : POST distinct du login', async () => {
  const store = storage()
  await startGitHubAccountAuth('link', { start: async intent => {
    assert.equal(intent, 'link')
    return { authorization_url: 'https://github.com/login/oauth/authorize', browser_binding: 'd'.repeat(50) }
  }, storage: store, redirect: () => {} })
  assert.equal(consumeGitHubAccountCallback('#status=linked', store).status, 'linked')
  assert.equal(store.getItem(GITHUB_BROWSER_BINDING_KEY), null)
})

test('login GitHub sans compte distingue le cas non inscrit', () => {
  assert.equal(consumeGitHubAccountCallback('#status=not_registered', storage()).status, 'not_registered')
})
