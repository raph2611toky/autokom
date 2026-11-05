import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, statSync } from 'node:fs'

const file = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const login = file('../src/pages/Login.jsx')
const register = file('../src/pages/Register.jsx')
const css = file('../src/styles/auth.css')
const icon = file('../src/components/GitHubMark.jsx')
const landing = file('../src/pages/Landing.jsx')
const authFrame = file('../src/components/AuthFrame.jsx')
const header = file('../src/components/Header.jsx')
const html = file('../index.html')

test('logo AUTOKOM fourni est présent dans les assets Vite et référencé sur tous les écrans', () => {
  const logo = readFileSync(new URL('../public/autokom-logo.png', import.meta.url))
  assert.ok(statSync(new URL('../public/autokom-logo.png', import.meta.url)).size > 10_000)
  assert.equal(logo.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
  assert.equal(logo.readUInt32BE(16), 512)
  assert.equal(logo.readUInt32BE(20), 488)
  for (const source of [landing, authFrame, header, html]) {
    assert.ok(source.includes('/autokom-logo.png'))
  }
})

test('GitHub possède une marque pleine qui s’adapte aux thèmes sans service distant', () => {
  assert.match(icon, /viewBox="0 0 16 16" fill="currentColor"/)
  assert.match(icon, /aria-hidden="true"/)
  assert.match(css, /\.auth-ui-github svg \{[^}]*var\(--ak-text-strong\)/)
  assert.match(file('../src/components/GitHubConnector.jsx'), /GitHubMark/)
})

test('le lien mot de passe oublié se trouve directement après le champ, avant le bouton principal', () => {
  const password = login.indexOf('id="login-password"')
  const forgot = login.indexOf('className="auth-ui-forgot"')
  const submit = login.indexOf('type="submit"')
  assert.ok(password >= 0 && forgot > password && submit > forgot)
  assert.match(login, /navigate\('\/forgot-password'\)/)
})

test('GitHub se trouve en dernière action du login et de la page inscription', () => {
  assert.doesNotMatch(login, /Configurer un projet/)
  assert.doesNotMatch(register, /Configurer un projet/)
  assert.equal((login.match(/Se connecter avec GitHub/g) || []).length, 1)
  assert.equal((register.match(/S’inscrire avec GitHub/g) || []).length, 1)
  assert.ok(login.indexOf('Se connecter avec GitHub') > login.indexOf('Créer un compte</button>'))
  assert.ok(register.indexOf('S’inscrire avec GitHub') > register.indexOf('Se connecter</button>'))
  assert.match(login, /API_AVAILABLE/)
  assert.match(register, /GITHUB_AVAILABLE/)
})
