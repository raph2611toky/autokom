import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const read = name => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8')

test('le checkout ne crée aucun droit local et refuse un résultat non confirmé', () => {
  const code = read('../src/pages/Checkout.jsx')
  assert.ok(code.includes('allowed !== true'))
  assert.ok(code.includes('result.payment_real !== false || result.amount_charged_ariary !== 0'))
  assert.doesNotMatch(code, /localStorage/)
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
