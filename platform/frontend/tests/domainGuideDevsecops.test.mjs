import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dnsGuideHTML } from '../src/services/domainGuide.js'

test('guide domaine externe inclut IPv4 dynamique et TXT sans secret',()=>{
  const result=dnsGuideHTML({hostname:'www.monsite.mg',domain_type:'CUSTOM',verification:{type:'TXT',hostname:'_autokom-challenge.www.monsite.mg',value:'autokom-verification=hash123'}},{ingress_ipv4:'203.0.113.11',managed_suffix:'auto.ikom.mg',dns_zone:'ikom.mg'})
  assert.match(result,/203\.0\.113\.11/)
  assert.match(result,/_autokom-challenge\.www\.monsite\.mg/)
  assert.match(result,/DNS uniquement/)
  assert.doesNotMatch(result,/CLOUDFLARE_API_TOKEN/)
})
test('guide échappe les contenus HTML et refuse une fausse IP',()=>{
  assert.throws(()=>dnsGuideHTML({hostname:'portfolio.auto.ikom.mg',domain_type:'SUBDOMAIN'},{ingress_ipv4:'{{TOKEN}}'}))
  const text=dnsGuideHTML({hostname:'portfolio.auto.ikom.mg',domain_type:'SUBDOMAIN'},{ingress_ipv4:'198.51.100.24',dns_zone:'<img src=x>'})
  assert.doesNotMatch(text,/<img src=x>/)
  assert.match(text,/&lt;img src=x&gt;/)
})
test('frontend demande explicitement le type et fournit un guide',()=>{
  const api=readFileSync(new URL('../src/services/projectsApi.js', import.meta.url),'utf8')
  const page=readFileSync(new URL('../src/pages/LiveSites.jsx',import.meta.url),'utf8')
  assert.match(api,/domain_type:domainType/)
  assert.match(page,/domainType/)
  assert.match(page,/dnsSetup\?\.ingress_ipv4/)
  assert.match(page,/Télécharger le guide DNS/)
})
