import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { readCookieConsent, saveCookieConsent, COOKIE_CONSENT_KEY } from '../src/services/cookieConsent.js'
const source = file => readFileSync(new URL(file, import.meta.url), 'utf8')
const contact = source('../src/pages/ContactSection.jsx')
const landing = source('../src/pages/Landing.jsx')
const banner = source('../src/components/CookieConsent.jsx')
const css = source('../src/styles/cookie-consent.css')

test('contact utilise POST /api/contact avec CSRF partagé, sans mailto ni stockage secret', () => {
  assert.match(contact, /api\.post\('\/contact\/'/)
  assert.match(contact, /retry: false/)
  assert.match(contact, /pending/)
  assert.match(contact, /role=\{feedback\.kind === 'error' \? 'alert' : 'status'\}/)
  assert.doesNotMatch(contact, /mailto:|navigator\.clipboard|localStorage|VITE_AUTOKOM_CONTACT_EMAIL/)
})

test('landing affiche une bannière avec réouverture et deux choix effectifs', () => {
  assert.match(landing, /CookieConsent visible=\{introState === 'done'\}/)
  assert.match(landing, /autokom:cookie-open/)
  assert.match(banner, /saveCookieConsent\(optional\)/)
  assert.match(banner, /Refuser les facultatifs/)
  assert.match(banner, /onClick=\{\(\) => decide\(true\)\}/)
  assert.match(css, /var\(--ak-surface\)/)
  assert.match(css, /prefers-reduced-motion/)
})

test('consentement mémorisé, refus et acceptation explicitement différents', () => {
  const store = new Map()
  const storage = { setItem: (k,v)=>store.set(k,v), getItem:k=>store.get(k) }
  assert.equal(readCookieConsent(storage), null)
  saveCookieConsent(false, storage)
  assert.equal(readCookieConsent(storage)?.optional, false)
  saveCookieConsent(true, storage)
  assert.equal(readCookieConsent(storage)?.optional, true)
  assert.equal(JSON.parse(store.get(COOKIE_CONSENT_KEY)).essential, true)
  store.set(COOKIE_CONSENT_KEY, JSON.stringify({version:0, optional:true, savedAt: Date.now()}))
  assert.equal(readCookieConsent(storage), null)
})
