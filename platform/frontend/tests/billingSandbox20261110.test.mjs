import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const code = readFileSync(fileURLToPath(new URL('../src/pages/Deploy.jsx', import.meta.url)), 'utf8')
const service = readFileSync(fileURLToPath(new URL('../src/services/billingApi.js', import.meta.url)), 'utf8')

test('la HA reste désactivée par défaut même avec une offre payante', async () => {
  const { planToDeployment } = await import('../src/config/pricing.js')
  assert.equal(planToDeployment('free').highAvailability, false)
  assert.equal(planToDeployment('essential').highAvailability, false)
  assert.equal(planToDeployment('pro').highAvailability, false)
})
test('la souscription test n’est pas un faux succès local', () => {
  assert.match(code, /await billingApi\.checkout/)
  assert.match(code, /result\.receipt\.mode !== 'SANDBOX'/)
  assert.match(code, /await saveRemoteProject\(\{ \.\.\.checked, backendProjectId/)
  assert.match(code, /Aucun moyen de paiement demandé et aucun argent débité/)
})
test('l’API transmet uniquement des paramètres bornés au backend', () => {
  assert.match(service, /\['essential','pro'\]/)
  assert.match(service, /uuid\(idempotencyKey\)/)
  assert.match(service, /billing\/sandbox\/checkout/)
})
