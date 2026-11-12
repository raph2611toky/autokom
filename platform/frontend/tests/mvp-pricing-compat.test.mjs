import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve, dirname } from 'node:path'
import { PRICING_PLANS } from '../src/config/pricing.js'

// Pricing.jsx contains JSX, so extract the pure exported helper without
// relying on a test transpiler or disabling the existing frontend tests.
const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(resolve(here, '../src/pages/Pricing.jsx'), 'utf8')
const match = source.match(/^export function activePricingPlan\(subscription\) \{[\s\S]*?^\}/m)
assert.ok(match, 'activePricingPlan must remain exported for existing consumers')
const activePricingPlan = new Function(
  'PRICING_PLANS',
  `${match[0].replace(/^export /, '')}\nreturn activePricingPlan;`
)(PRICING_PLANS)

test('absence of a subscription does not select a fabricated plan', () => {
  assert.equal(activePricingPlan(null), null)
  assert.equal(activePricingPlan(undefined), null)
  assert.equal(activePricingPlan('free'), null)
})

test('the free launch entitlement never appears as a paid plan', () => {
  assert.equal(activePricingPlan({launch_offer: {offer: 'Lancement'}, plan: 'pro', active: true}), 'free')
})

test('legacy plan identifiers retain explicit validity checks', () => {
  assert.equal(activePricingPlan({active: true, plan: 'pro'}), 'pro')
  assert.equal(activePricingPlan({active: true, plan: 'essential'}), 'essential')
  assert.equal(activePricingPlan({active: false, plan: 'pro'}), 'free')
  assert.equal(activePricingPlan({active: true, plan: 'unrecognized'}), null)
})
