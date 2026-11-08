import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const read = p => fs.readFileSync(new URL(p, import.meta.url), 'utf8')

test('GitHub installs keep AUTOKOM tab and refresh permissions after Save', () => {
  const ui = read('../src/components/LiveGitHubConnector.jsx')
  assert.match(ui, /target="_blank" rel="noopener noreferrer"/)
  assert.match(ui, /setPermissionPending\(true\)/)
  assert.match(ui, /gitApi\.installations\(\)/)
  assert.match(ui, /gitApi\.repositories\(preferred\.id\)/)
  assert.match(ui, /window\.addEventListener\('focus'/)
  assert.match(ui, /Actualiser maintenant/)
  assert.doesNotMatch(ui, /window\.location\.assign\(verifiedInstallationUrl\(installationUrl\)\)/)
})

test('SQLite backend source is rejected by Django preview and frontend submit', () => {
  const ui=read('../src/pages/Deploy.jsx')
  assert.match(ui, /analysis\?\.storageGuard\?\.blocked/)
  assert.match(ui, /analysis\.storageGuard\.message/)
  const connector=read('../src/components/LiveGitHubConnector.jsx')
  assert.match(connector, /browseAnalysis\?\.blocked/)
})
