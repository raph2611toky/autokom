import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_SERVICES, SERVICE_GROUPS, resolveCapability } from '../src/config/serviceCatalog.js'

test('chaque groupe possède une sélection initiale valide', () => {
  assert.equal(SERVICE_GROUPS.length, 6)
  for (const group of SERVICE_GROUPS) {
    assert.ok(group.options.some((option) => option.id === DEFAULT_SERVICES[group.id]), group.id)
    assert.equal(new Set(group.options.map((option) => option.id)).size, group.options.length)
  }
})

test('le catalogue distingue les types d’offres', () => {
  const all = SERVICE_GROUPS.flatMap((group) => group.options)
  assert.ok(all.some((service) => service.tier === 'libre'))
  assert.ok(all.some((service) => service.tier === 'quota'))
  assert.ok(all.some((service) => service.tier === 'payant'))
  assert.ok(all.every((service) => !service.url || service.url.startsWith('https://')))
})

test('une intégration non déclarée ne doit jamais être affichée comme disponible', () => {
  assert.equal(resolveCapability([], 'security.trivy'), 'unknown')
  assert.equal(resolveCapability([], null), 'not-required')
  assert.equal(resolveCapability([{ key: 'security.trivy', enabled: false, status: 'healthy' }], 'security.trivy'), 'disabled')
  assert.equal(resolveCapability([{ key: 'security.trivy', status: 'healthy' }], 'security.trivy'), 'healthy')
  assert.equal(resolveCapability([{ key: 'security.trivy', status: 'degraded' }], 'security.trivy'), 'unavailable')
})
