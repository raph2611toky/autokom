import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../src/pages/LiveSites.jsx', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/services/projectsApi.js', import.meta.url), 'utf8')
test('active domains can use controlled migration instead of permanently disabled save', () => {
  assert.doesNotMatch(source, /domain\.status !== 'PENDING' \|\| domain\.verified \|\| domain\.https \|\| domain\.is_disabled/)
  assert.match(source, /availability\.state !== 'available'/)
  assert.match(source, /domain\.https \? 'Nouveau domaine actif en HTTPS/)
})
test('live availability comes from authenticated backend and is debounced and cancelled', () => {
  assert.match(source, /projectsApi\.domainAvailability/)
  assert.match(source, /window\.setTimeout\(/)
  assert.match(source, /controller\.abort\(\)/)
  assert.match(api, /domainAvailability:/)
})
test('input has inline spinner, success check icon, red rejection and status message', () => {
  assert.match(source, /ak-availability-\$\{availability\.state\}/)
  assert.match(source, /availability\.state === 'checking' \? <LoaderCircle/)
  assert.match(source, /availability\.state === 'available' \? <CheckCircle2/)
  assert.match(source, /availability\.state === 'unavailable' \? <XCircle/)
  assert.match(source, /role="status" aria-live="polite"/)
})
test('previously registered hostname in this project is identified as reusable, foreign hostname is refused', () => {
  assert.match(source, /owned_by_project: 'Ce sous-domaine appartient déjà à ce projet/)
  assert.match(source, /reserved_other_project: 'Ce sous-domaine est déjà attribué à un autre projet/)
  assert.match(source, /dns_target_mismatch: 'Domaine enregistré dans ce projet, mais son DNS pointe ailleurs/)
  assert.match(source, /availability.state !== 'available'/)
})
test('the confirmation message for deleting managed domain states DNS removal', () => {
  assert.match(source, /Domaine et enregistrement DNS AUTOKOM supprimés/)
})
