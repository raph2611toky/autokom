import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const read = path => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')

test('l’offre gratuite de lancement est sélectionnée dans Tarifs et conduit à Déployer sans checkout', () => {
  const pricing = read('../src/pages/Pricing.jsx')
  assert.match(pricing, /Offre gratuite de lancement/)
  assert.match(pricing, /onClick=\{\(\) => navigate\('\/deploy'\)\}/)
  assert.match(pricing, /Gratuit/)
  assert.ok(pricing.includes('disabled={!canSubscribe}'))
  assert.ok(pricing.includes('/checkout/'))
  assert.doesNotMatch(pricing, /\/checkout\/\$\{id\}|billingApi\.(?:checkout|accountCheckout)|activateSandboxSubscription/)

  const deploy = read('../src/pages/Deploy.jsx')
  assert.doesNotMatch(deploy, /activateSandboxSubscription|Activer.*mode TEST|billingApi\.checkout/)
  assert.match(deploy, /Abonnement du compte en lecture seule/)
  assert.match(deploy, /billingApi\.account\(\)/)
})

test('le checkout vérifie le droit du compte et le résultat du backend', () => {
  const source = read('../src/pages/Checkout.jsx')
  assert.ok(source.includes('subscription?.can_subscribe === true'))
  assert.ok(source.includes('billingApi.accountCheckout(plan.id, period, key)'))
  assert.ok(source.includes("navigate('/account')"))
  assert.doesNotMatch(source, /mode test|localStorage/i)
})

test('la HA reste une décision séparée du plan de compte', () => {
  const source = read('../src/pages/Deploy.jsx')
  assert.match(source, /highAvailability: activePlan === 'pro' && Boolean\(old.highAvailability\)/)
  assert.match(source, /accountSubscription\?\.ha_available/)
})

test('Tarifs retrouve ses trois cartes et les quatre durées restent dans Découverte', () => {
  const pricing = read('../src/pages/Pricing.jsx')
  assert.match(pricing, /PRICING_PLANS\.map\(plan/)
  assert.match(pricing, /Souscription bientôt disponible/)
  assert.match(pricing, /Frontend uniquement/)
  assert.match(pricing, /Frontend \+ backend/)
  assert.match(pricing, /30 jours/)
  assert.match(pricing, /15 jours/)
  assert.match(pricing, /7 jours/)
})
