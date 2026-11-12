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
  assert.doesNotMatch(pricing, /\/checkout\/\$\{id\}|billingApi\.(?:checkout|accountCheckout)|activateSandboxSubscription/)

  const deploy = read('../src/pages/Deploy.jsx')
  assert.doesNotMatch(deploy, /activateSandboxSubscription|Activer.*mode TEST|billingApi\.checkout/)
  assert.match(deploy, /Abonnement du compte en lecture seule/)
  assert.match(deploy, /billingApi\.account\(\)/)
})

test('le checkout public annonce l’indisponibilité du paiement et ne simule aucune activation', () => {
  const source = read('../src/pages/Checkout.jsx')
  assert.match(source, /Offre gratuite de lancement/)
  assert.match(source, /Le paiement en ligne n'est pas encore disponible/)
  assert.match(source, /sans renseigner de moyen de paiement/)
  assert.match(source, /navigate\('\/deploy'\)/)
  assert.doesNotMatch(source, /billingApi\.(?:accountCheckout|checkout)|activateSandboxSubscription/)
  assert.doesNotMatch(source, /amount_charged_ariary|payment_real|localStorage\.setItem/)
})

test('la HA reste une décision séparée du plan de compte', () => {
  const source = read('../src/pages/Deploy.jsx')
  assert.match(source, /highAvailability: activePlan === 'pro' && Boolean\(old.highAvailability\)/)
  assert.match(source, /accountSubscription\?\.ha_available/)
})

test('les quatre durées de la MVP sont visibles dans Tarifs', () => {
  const pricing = read('../src/pages/Pricing.jsx')
  assert.match(pricing, /Frontend uniquement/)
  assert.match(pricing, /Frontend \+ backend/)
  assert.match(pricing, /30 jours/)
  assert.match(pricing, /15 jours/)
  assert.match(pricing, /7 jours/)
})
