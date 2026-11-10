import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const read = path => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')

test('la souscription est demandée dans Tarifs et non dans Déployer', () => {
  assert.match(read('../src/pages/Pricing.jsx'), /\/checkout\/\$\{id\}/)
  const source = read('../src/pages/Deploy.jsx')
  assert.doesNotMatch(source, /activateSandboxSubscription|Activer.*mode TEST|billingApi\.checkout/)
  assert.match(source, /Abonnement du compte en lecture seule/)
  assert.match(source, /billingApi\.account\(\)/)
})
test('le checkout ne simule pas un vrai débit et exige un consentement explicite', () => {
  const source = read('../src/pages/Checkout.jsx')
  assert.match(source, /Aucun(e)? carte bancaire|Aucune carte bancaire/)
  assert.match(source, /amount_charged_ariary !== 0/)
  assert.match(source, /!accepted/)
  assert.match(source, /billingApi\.accountCheckout/)
})
test('la HA reste une décision séparée du plan de compte', () => {
  const source = read('../src/pages/Deploy.jsx')
  assert.match(source, /highAvailability: activePlan === 'pro' && Boolean\(old.highAvailability\)/)
  assert.match(source, /accountSubscription\?\.ha_available/)
})
