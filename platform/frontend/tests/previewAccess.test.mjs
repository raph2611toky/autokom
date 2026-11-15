import assert from 'node:assert/strict'
import { test } from 'node:test'
import { projectsApi } from '../src/services/projectsApi.js'
import { siteStateLabel, publicSiteUrl } from '../src/services/sitePresentation.js'

const env = 'a11e7777-4321-4f00-9070-00a3002001ba'

test('le fichier WireGuard reste une pièce jointe authentifiée et non mise en cache', async () => {
  const previous = {fetch:globalThis.fetch, document:globalThis.document}
  globalThis.document = { cookie:'csrftoken=test-csrf' }
  try {
    globalThis.fetch = async (path, options) => {
      assert.equal(path, '/api/projects/' + env + '/environments/' + env + '/preview-access/credentials/')
      assert.equal(options.method, 'POST')
      assert.equal(options.cache, 'no-store')
      assert.equal(options.credentials, 'same-origin')
      assert.equal(options.headers['X-CSRFToken'], 'test-csrf')
      return new Response('[Interface]\nPrivateKey = test-only', {headers:{'Content-Type':'application/octet-stream'}})
    }
    const file = await projectsApi.issuePreviewAccess(env, env)
    assert.ok(file instanceof Blob)
    assert.match(await file.text(), /^\[Interface\]/)
  } finally {
    globalThis.fetch = previous.fetch
    if (previous.document === undefined) delete globalThis.document
    else globalThis.document = previous.document
  }
})

test('une adresse WireGuard ne devient pas un lien public', () => {
  const site = {wireguard_only:true, https:true, status:'ACTIVE', url:null,
    vpn_url:'https://pre.demo.auto.ikom.mg/'}
  assert.equal(publicSiteUrl(site), null)
  assert.equal(siteStateLabel(site), 'WireGuard uniquement')
})
