import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
const read = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8')

test('the final submission shows an explicit storage-risk dialog only for detected SQLite', () => {
  const deploy = read('../src/pages/Deploy.jsx')
  assert.match(deploy, /if \(!sqliteApproved && analysisStatus === 'ready' && analysis\?\.storageGuard\?\.detected\)/)
  assert.ok(deploy.slice(deploy.indexOf('async function previewPipeline')).indexOf('setSqliteConfirmOpen(true)') < deploy.slice(deploy.indexOf('async function previewPipeline')).indexOf('saveRemoteProject({ ...checked'))
  assert.match(deploy, /onConfirm=\{\(\) => \{ setSqliteConfirmOpen\(false\); void previewPipeline\(true\) \}\}/)
  assert.match(deploy, /SQLite détectée — données non persistantes/)
  assert.doesNotMatch(deploy, /storageGuard\?\.blocked/)
})

test('all production confirmations use theme-aware accessible dialog, not native browser dialogs', () => {
  const dialog = read('../src/components/ActionDialog.jsx')
  const css = read('../src/styles/dialogs.css')
  assert.match(dialog, /role="dialog" aria-modal="true"/)
  assert.match(dialog, /requiredText/)
  assert.match(dialog, /event.key === 'Escape'/)
  assert.match(css, /var\(--ak-surface\)/)
  assert.match(css, /var\(--ak-danger\)/)
  const sources = [
    '../src/pages/Deploy.jsx', '../src/pages/LiveProjectSettings.jsx',
    '../src/pages/LiveSites.jsx', '../src/pages/Projects.jsx',
    '../src/components/LiveGitHubConnector.jsx',
  ]
  for (const src of sources) {
    const data=read(src)
    assert.match(data, /ActionDialog/)
    assert.doesNotMatch(data, /(?:window\.)?(?:confirm|alert|prompt)\(/)
  }
  assert.match(read('../src/main.jsx'), /styles\/dialogs.css/)
})
