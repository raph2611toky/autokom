import test from 'node:test'
import assert from 'node:assert/strict'
import {detectSource,recommendFrontendCommands} from '../src/services/sourceAnalysis.js'
import {parseFrontendEnv,normalizeFrontendEnv,publicEnvForFramework} from '../src/services/frontendEnv.js'
const files = names => names.map(name=>({name,type:'file'}))

test('React + Vite with lockfile: build recommendations and supported',()=>{
  const items=files(['package.json','package-lock.json','vite.config.ts','src'])
  const analysis=detectSource(items,{dependencies:{react:'*'},devDependencies:{vite:'*'},scripts:{build:'configured'}})
  const result=recommendFrontendCommands(analysis,items)
  assert.equal(analysis.framework,'React');assert.equal(analysis.isVite,true)
  assert.equal(result.build,'npm run build');assert.equal(result.output,'dist');assert.equal(result.supported,true)
})
test('Next.js SSR identified as approved Node profile when package lock is available',()=>{
  const items=files(['package.json','package-lock.json'])
  const analysis=detectSource(items,{dependencies:{next:'*',react:'*'},scripts:{build:'configured',start:'configured'}})
  assert.equal(analysis.framework,'Next.js'); assert.equal(recommendFrontendCommands(analysis,items).supported,true)
})
test('static HTML recognized and no misleading npm command',()=>{
  const analysis=detectSource(files(['index.html','style.css']))
  const advice=recommendFrontendCommands(analysis,files(['index.html']))
  assert.equal(advice.install,'Non nécessaire');assert.equal(advice.output,'./')
})
test('dotenv: quoted keys, export and comments; import preserves ampersand and hash',()=>{
  assert.deepEqual(parseFrontendEnv('export VITE_API_URL="https://example.org/x?a=1&b=2"\n# commentaire\nVITE_TITLE=Bonjour # note\nVITE_HASH=foo#bar\n'),[
    {key:'VITE_API_URL',value:'https://example.org/x?a=1&b=2'},
    {key:'VITE_TITLE',value:'Bonjour'}, {key:'VITE_HASH',value:'foo#bar'},
  ])
})
test('dotenv: secrets, duplicates, multiline and command expansions refused',()=>{
  for (const value of ['SECRET_KEY=abc','VITE_API_TOKEN=abc','VITE_A=1\nVITE_A=2','VITE_A=$(curl x)','VITE_A="unterminated']) {
    assert.throws(()=>parseFrontendEnv(value))
  }
  assert.throws(()=>normalizeFrontendEnv([{key:'VITE_FOO',value:'\nx'}]))
})
test('Vite env accepts only public VITE prefix; Next public vars are not deployed',()=>{
  const source={isVite:true,applicationType:'static'}
  assert.deepEqual(publicEnvForFramework([{key:'VITE_SITE_NAME',value:'Portfolio'}],source),[{key:'VITE_SITE_NAME',value:'Portfolio'}])
  assert.throws(()=>publicEnvForFramework([{key:'NEXT_PUBLIC_SITE_NAME',value:'Portfolio'}],source))
  assert.throws(()=>publicEnvForFramework([{key:'VITE_A',value:'a'}],{isVite:false,applicationType:'service'}))
})
