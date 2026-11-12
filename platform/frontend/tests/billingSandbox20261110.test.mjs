import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const read = name => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8')

test('les droits de paiement ne peuvent pas être accordés par le checkout public indisponible', () => {
  const code = read('../src/pages/Checkout.jsx')
  assert.match(code, /Le paiement en ligne n'est pas encore disponible/)
  assert.match(code, /sans renseigner de moyen de paiement/)
  assert.doesNotMatch(code, /billingApi\.(?:accountCheckout|checkout)|activateSandboxSubscription/)
  assert.doesNotMatch(code, /localStorage\.setItem\([^)]*subscription/)
  assert.doesNotMatch(code, /response\?\.subscription\?\.active|payment_real !== false/)
})

test('la HA est facultative dans toutes les offres', async () => {
  const { planToDeployment } = await import('../src/config/pricing.js')
  for (const name of ['free', 'essential', 'pro']) assert.equal(planToDeployment(name).highAvailability, false)
})

test('l’API de paiement est liée au compte et valide les valeurs côté client', () => {
  // L’API reste disponible dans le code pour une future intégration de paiement.
  // La page Checkout ne doit pas l’invoquer avant l’activation du vrai prestataire.
  const service = read('../src/services/billingApi.js')
  assert.match(service, /billing\/account\/checkout\//)
  assert.match(service, /uuid\(key\)/)
  assert.match(service, /\['essential', 'pro'\]/)
})
