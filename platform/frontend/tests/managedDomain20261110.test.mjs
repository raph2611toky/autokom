import test from 'node:test'
import assert from 'node:assert/strict'
import { AUTOKOM_DOMAIN_SUFFIX, managedDomainHost, managedDomainPrefix } from '../src/services/managedDomain.js'

test('only the prefix can be entered for an AUTOKOM subdomain', () => {
  assert.equal(AUTOKOM_DOMAIN_SUFFIX, 'auto.ikom.mg')
  assert.equal(managedDomainHost('Lady'), 'lady.auto.ikom.mg')
  assert.equal(managedDomainPrefix('portfolio.auto.ikom.mg'), 'portfolio')
  assert.throws(() => managedDomainHost('portfolio.auto.ikom.mg'), /préfixe/)
  assert.throws(() => managedDomainHost('../fraud'), /préfixe/)
  assert.throws(() => managedDomainHost('a', 'auto.ik0m.tech'), /préfixe/)
  assert.equal(managedDomainPrefix('portfolio.evil.example'), '')
})
