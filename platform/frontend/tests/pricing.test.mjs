import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_PRICING_PLAN, findPricingPlan, getPreferredPlan, planToDeployment, PRICING_PLANS, rememberPlan, formatAr } from '../src/config/pricing.js'

function fakeStorage(values = {}) {
  return {
    values,
    getItem: key => values[key] ?? null,
    setItem: (key, value) => { values[key] = String(value) },
  }
}

test('trois tarifs distincts dont une formule gratuite', () => {
  assert.deepEqual(PRICING_PLANS.map(plan => plan.id), ['free','essential','pro'])
  assert.equal(findPricingPlan('free').monthly, 0)
  assert.equal(findPricingPlan('essential').annual, findPricingPlan('essential').monthly * 10)
  assert.equal(findPricingPlan('pro').annual, findPricingPlan('pro').monthly * 10)
})

test('le choix du tarif configure une offre cohérente dans Déployer', () => {
  assert.deepEqual(planToDeployment('free'), { pricingPlan:'free', resourcePlan:'free', highAvailability:false })
  assert.deepEqual(planToDeployment('essential'), { pricingPlan:'essential', resourcePlan:'adapted', highAvailability:false })
  assert.deepEqual(planToDeployment('pro'), { pricingPlan:'pro', resourcePlan:'adapted', highAvailability:true })
})

test('la formule sélectionnée est persistée pour le parcours frontend', () => {
  const storage = fakeStorage()
  rememberPlan('pro', storage)
  assert.equal(getPreferredPlan(storage),'pro')
  assert.equal(storage.values['autokom.ui.preferred-offer'],'adapted')
  rememberPlan('free', storage)
  assert.equal(getPreferredPlan(storage),'free')
})

test('l’ancien choix adapté reste compatible et une valeur inconnue est neutralisée', () => {
  const storage = fakeStorage({ 'autokom.ui.preferred-offer':'adapted' })
  assert.equal(getPreferredPlan(storage),'essential')
  assert.equal(findPricingPlan('invalid').id,DEFAULT_PRICING_PLAN)
  assert.ok(formatAr(49000).includes('Ar'))
})

test('les brouillons des versions précédentes retrouvent leur formule', async () => {
  const { inferPricingPlan } = await import('../src/config/pricing.js')
  assert.equal(inferPricingPlan({resourcePlan:'free'}),'free')
  assert.equal(inferPricingPlan({resourcePlan:'adapted',highAvailability:false}),'essential')
  assert.equal(inferPricingPlan({resourcePlan:'adapted',highAvailability:true}),'pro')
  assert.equal(inferPricingPlan({pricingPlan:'pro',resourcePlan:'adapted'}),'pro')
})

test('la périodicité annuelle suit le choix du tarif', async () => {
  const { getBillingPeriod } = await import('../src/config/pricing.js')
  const storage = fakeStorage()
  rememberPlan('essential',storage,'annual')
  assert.equal(getBillingPeriod(storage),'annual')
  rememberPlan('essential',storage,'monthly')
  assert.equal(getBillingPeriod(storage),'monthly')
})
