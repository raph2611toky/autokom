import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/LiveSites.jsx', import.meta.url), 'utf8')

test('editing belongs to selected domain row, not add form', () => {
  const start = source.indexOf('{domains.map(domain => {')
  const inline = source.indexOf('className="ak-domain-inline-edit"')
  const end = source.indexOf('</article>', start)
  const addForm = source.indexOf('<form onSubmit={addDomain}')
  assert.ok(start >= 0 && inline > start && end > inline && addForm > end)
  assert.match(source, /editingDomain === domain\.id && <form/)
})

test('managed project creation is constrained and suffix is immutable', () => {
  assert.match(source, /const managedAlreadyReserved = domains\.some/)
  assert.match(source, /const createDomainType = managedAlreadyReserved \? 'CUSTOM' : domainType/)
  assert.match(source, /managedDomainHost\(editHostname/)
  assert.match(source, /<span>\.\{AUTOKOM_DOMAIN_SUFFIX\}<\/span>/)
})

test('delete remains visible for two domains, but is disabled for last domain or primary, and requires exact hostname', () => {
  assert.match(source, /disabled=\{busy \|\| domain\.is_primary \|\| domains\.length <= 1\}/)
  assert.doesNotMatch(source, /domains\.length <= 2/)
  assert.match(source, /requiredText=\{domainToDelete\?\.hostname \|\| ''\}/)
})
