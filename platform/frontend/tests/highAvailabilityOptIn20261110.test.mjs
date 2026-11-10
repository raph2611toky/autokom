import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { planToDeployment, getPreferredPlan, PRICING_PLANS } from '../src/config/pricing.js'
import { toBackendConfiguration } from '../src/services/projectWorkflow.js'

const frontend = readFileSync(new URL('../src/pages/Deploy.jsx', import.meta.url), 'utf8')
const backend = readFileSync(new URL('../../backend/apps/projects/configurations/services.py', import.meta.url), 'utf8')

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

test('UX explicite et garde serveur maintenu', () => {
  assert.match(frontend, /Haute disponibilité \(facultative\)/)
  assert.match(frontend, /Désactivée par défaut/)
  assert.match(frontend, /checked=\{Boolean\(form.highAvailability\)\}/)
  assert.match(backend, /if getattr\(cfg, 'high_availability_requested', False\):/)
  assert.match(backend, /if granted.max_instances < 2:/)
  assert.match(backend, /HighAvailabilityOption.objects.filter/)
  assert.match(backend, /HighAvailabilityOption.Status.ACTIVE/)
})
