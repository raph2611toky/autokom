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

test('SQLite est un avertissement uniquement au récapitulatif, avec confirmation explicite', () => {
  const deploy=read('../src/pages/Deploy.jsx')
  assert.match(deploy, /analysis\?\.storageGuard\?\.detected/)
  assert.match(deploy, /setSqliteConfirmOpen\(true\)/)
  assert.match(deploy, /previewPipeline\(true\)/)
  assert.match(deploy, /SQLite détectée — risque de perte de données/)
  assert.doesNotMatch(deploy, /storageGuard\?\.blocked/)
  const connector=read('../src/components/LiveGitHubConnector.jsx')
  assert.doesNotMatch(connector, /browseAnalysis\?\.blocked/)
  assert.match(connector, /storageWarning/)
})
