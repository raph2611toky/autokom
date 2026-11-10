import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const read = name => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8')

test('le backend reste le seul responsable des droits de paiement', () => {
  const code = read('../src/pages/Checkout.jsx')
  assert.match(code, /await billingApi\.accountCheckout/)
  assert.match(code, /response\?\.subscription\?\.active/)
  assert.match(code, /payment_real !== false/)
  assert.doesNotMatch(code, /localStorage\.setItem\([^)]*subscription/)
})
test('la HA est facultative dans toutes les offres', async () => {
  const { planToDeployment } = await import('../src/config/pricing.js')
  for (const name of ['free', 'essential', 'pro']) assert.equal(planToDeployment(name).highAvailability, false)
})
test('l’API de paiement est liée au compte et valide les valeurs côté client', () => {
  const service = read('../src/services/billingApi.js')
  assert.match(service, /billing\/account\/checkout\//)
  assert.match(service, /uuid\(key\)/)
  assert.match(service, /\['essential', 'pro'\]/)
})
