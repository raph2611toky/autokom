import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { publicSiteUrl, siteStateLabel } from '../src/services/sitePresentation.js'

const active = {hostname:'portfolio.auto.ikom.mg',url:'https://portfolio.auto.ikom.mg',status:'ACTIVE',https:true}
test('adresse publiée seulement si DNS et TLS sont actifs', () => {
  assert.equal(publicSiteUrl(active),'https://portfolio.auto.ikom.mg/')
  assert.equal(publicSiteUrl({...active,status:'PENDING'}),null)
  assert.equal(publicSiteUrl({...active,https:false}),null)
  assert.equal(publicSiteUrl({...active,url:'javascript:alert(1)'}),null)
  assert.equal(publicSiteUrl({...active,url:'https://evil.org@other.org/'}),null)
  assert.equal(publicSiteUrl({...active,url:'https://portfolio.auto.ikom.mg:8443/'}),null)
  assert.equal(siteStateLabel({...active,status:'PENDING'}),'Adresse réservée · DNS/HTTPS en attente')
})

test('l’interface utilise les API réelles de domaines et suppression', () => {
  const api=readFileSync(new URL('../src/services/projectsApi.js',import.meta.url),'utf8')
  for(const method of ['createDomain','verifyDomain','primaryDomain','deactivateDomain','deleteProject','restore'])
    assert.ok(api.includes(`${method}:`),method)
  for(const page of ['LiveProjects.jsx','LiveSites.jsx','LiveRuns.jsx']) {
    const src=readFileSync(new URL(`../src/pages/${page}`,import.meta.url),'utf8')
    assert.ok(src.includes('publicSiteUrl') || src.includes('ProjectPublicSite'),page)
  }
})
