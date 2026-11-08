import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const source = file => readFileSync(new URL(file, import.meta.url), 'utf8')
const landing = source('../src/pages/Landing.jsx')
const app = source('../src/App.jsx')
const auth = source('../src/context/AuthContext.jsx')
const api = source('../src/services/api.js')
const css = source('../src/styles/sites.css')
const landingCss = source('../src/styles/landing.css')

test('la landing utilise la session React existante et propose directement le dashboard', () => {
  assert.match(landing, /useAuth\(\)/)
  assert.match(landing, /sessionStatus === 'authenticated'/)
  assert.match(landing, /navigate\(connected \? '\/dashboard' : '\/login'\)/)
  assert.match(landing, /navigate\(connected \? '\/dashboard' : '\/register'\)/)
  assert.match(landing, /Accéder à mon espace/)
  assert.match(landing, /checkingSession/)
  assert.match(landingCss, /lp-session-active/)
})

test('login et inscription redirigent seulement un utilisateur authentifié vers le dashboard', () => {
  assert.match(app, /status === 'authenticated'/)
  assert.match(app, /route === '\/login' \|\| route === '\/register'/)
  assert.match(app, /navigate\('\/dashboard'\)/)
})

test('cookies HttpOnly et rotation de session, jamais de jeton dans localStorage', () => {
  assert.match(auth, /authApi\.me\(\)/)
  assert.match(api, /credentials: 'same-origin'/)
  assert.match(api, /refreshSession\(\)/)
  assert.doesNotMatch(landing, /localStorage\.setItem\(['"](?:access|refresh|token)/)
  assert.doesNotMatch(auth, /localStorage\.setItem/)
})

test('les listes de projets et sites ont une grille flexible et les actions restent dedans', () => {
  assert.match(css, /\.ak-sites-simple-list \.ak-project-list \.ak-project-row/)
  assert.match(css, /minmax\(0,1fr\) minmax\(0, min\(260px, 32%\)\) max-content/)
  assert.match(css, /\.ak-project-row > \.button-outline/)
  assert.match(css, /grid-column:2 \/ -1; grid-row:2/)
  assert.match(css, /overflow-wrap:anywhere/)
})
