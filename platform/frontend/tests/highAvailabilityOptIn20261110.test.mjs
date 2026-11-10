import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { planToDeployment, PRICING_PLANS } from '../src/config/pricing.js'
import { toBackendConfiguration } from '../src/services/projectWorkflow.js'

const frontend = readFileSync(new URL('../src/pages/Deploy.jsx', import.meta.url), 'utf8')
// This test is executed in the isolated frontend build workspace, which contains
// only src/, public/, and tests/. Backend entitlement rules are tested separately
// by Django's test_ha_entitlement_privileges.py (not by this frontend build).

test('aucun plan ne coche implicitement la HA, y compris Professionnel', () => {
  for (const plan of PRICING_PLANS) {
    assert.equal(planToDeployment(plan.id).highAvailability, false, plan.id)
  }
})

test('la demande HA ne naît que du choix explicite du client', () => {
  const form={ branch:'main',workdir:'./',pipelineMode:'devops',detectedFramework:'React',detectedVite:true,
    hasNpmLockfile:true,frontendProfile:'vite',advancedBuild:{profile:'vite'} }
  assert.equal(toBackendConfiguration({...form,...planToDeployment('pro')}).high_availability_requested,false)
  assert.equal(toBackendConfiguration({...form,...planToDeployment('pro'),highAvailability:true}).high_availability_requested,true)
})

test('UX explicite : la HA reste facultative et décorrélée du tarif', () => {
  assert.match(frontend, /Haute disponibilité \(facultative\)/)
  assert.match(frontend, /Désactivée par défaut/)
  assert.match(frontend, /checked=\{Boolean\(form.highAvailability\)\}/)
})
