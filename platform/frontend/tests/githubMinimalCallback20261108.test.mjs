import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = relative => readFileSync(new URL(relative, import.meta.url), 'utf8')
const jsx = source('../src/pages/GitHubAuthCallback.jsx')
const css = source('../src/styles/githubCallback.css')

test('callback OAuth: GitHub → spinner → logo AUTOKOM uniquement', () => {
  assert.match(jsx, /<GitHubMark size=\{39\}/)
  assert.match(jsx, /ak-github-transfer-spinner/)
  assert.match(jsx, /src="\/autokom-logo\.png"/)
  assert.doesNotMatch(jsx, /<AuthFrame|ak-github-steps|STEPS\.map/)
  assert.match(css, /place-items: center/)
  assert.match(css, /prefers-reduced-motion: reduce/)
})

test('échange et session GitHub inchangés : state/code consommés avant le réseau', () => {
  assert.match(jsx, /consumeGitHubAccountCallback\(/)
  assert.match(jsx, /history\.replaceState/)
  assert.ok(jsx.indexOf('history.replaceState') < jsx.indexOf('await authApi.githubComplete'))
  assert.match(jsx, /await authApi\.githubComplete\(callback\)/)
  assert.match(jsx, /await authApi\.githubExchange\(callback\)/)
  assert.match(jsx, /await refreshUser\(\)/)
  assert.match(jsx, /navigate\('\/dashboard'\)/)
  assert.match(jsx, /started\.current/)
  assert.doesNotMatch(jsx, /localStorage|setItem\(.+token/i)
})
