import test from 'node:test'
import assert from 'node:assert/strict'
import { FRONTEND_ONLY, API_AVAILABLE, GITHUB_AVAILABLE } from '../src/config/runtime.js'

test('Le frontend fonctionne seul sans créer de connexion artificielle', () => {
  assert.equal(FRONTEND_ONLY, true)
  assert.equal(API_AVAILABLE, false)
  assert.equal(GITHUB_AVAILABLE, false)
})
