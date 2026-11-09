import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {frontendApiVariable, validFrontendApiKey, validBackendOriginKey, withoutManagedFields} from '../src/services/stackDomains.js'

const source = file => readFileSync(new URL(`../src/${file}`,import.meta.url), 'utf8')

test('les valeurs .env managées suivent les noms de domaine et non une valeur envoyée par le navigateur', () => {
  assert.deepEqual(frontendApiVariable('next_server','api.alpha.auto.ikom.mg'), {
    key:'NEXT_PUBLIC_API_URL',value:'https://api.alpha.auto.ikom.mg/api',
  })
  assert.deepEqual(frontendApiVariable('next_server','api.beta.example.com','NEXT_PUBLIC_BACKEND_URL'), {
    key:'NEXT_PUBLIC_BACKEND_URL',value:'https://api.beta.example.com/api',
  })
  assert.equal(validFrontendApiKey('next_server','NEXT_PUBLIC_BACKEND_URL'),true)
  assert.equal(validFrontendApiKey('vite','NEXT_PUBLIC_BACKEND_URL'),false)
  assert.equal(validFrontendApiKey('next_server','NEXT_PUBLIC_SECRET_KEY'),false)
  assert.equal(validFrontendApiKey('next_server','NEXT_PUBLIC_API_KEY'),false)
  assert.equal(validFrontendApiKey('npm_static','VUE_APP_URL'),true)
  assert.equal(validBackendOriginKey('CORS_ALLOWED_ORIGIN'),true)
  assert.equal(validBackendOriginKey('FRONTEND_ORIGIN'),true)
  assert.equal(validBackendOriginKey('PORT'),false)
  assert.equal(validBackendOriginKey('NODE_ENV'),false)
  assert.equal(validBackendOriginKey('DOCKER_HOST'),false)
  assert.equal(validBackendOriginKey('DB_PASSWORD'),false)
})

test('éditeur .env affiche les valeurs calculées non modifiables et verrouille PORT',()=>{
  const page=source('pages/StackDeploy.jsx')
  assert.match(page,/managed\.map\(\(item,index\) =>/)
  assert.match(page,/value=\{item\.value\} readOnly/)
  assert.match(page,/value=\{item\.key\} readOnly=\{item\.fixed\}/)
  assert.match(page,/key:'PORT',value:'8000',fixed:true/)
  assert.match(page,/onManagedKeyChange/)
  assert.match(page,/api_env_key:frontApiKey/)
  assert.match(page,/origin_env_key:backOriginKey/)
  assert.doesNotMatch(page,/ak-stack-domain-preview/)
  assert.doesNotMatch(page,/DNS géré par AUTOKOM/)
  assert.doesNotMatch(page,/ak-stack-runtime-contract/)
  assert.match(page,/stackApi\.preflightDomains\(projectId\)/)
  assert.ok(page.indexOf('stackApi.preflightDomains(projectId)') < page.indexOf('stackApi.launch(projectId,key)'))
})

// This file also runs inside AUTOKOM's isolated frontend build, where backend
// source files are deliberately absent. The backend .env persistence contract
// is verified separately in tests/test_autokom_stack_managed_env_20261109.py.
test('les variables libres restent éditables et les variables générées sont réservées',()=>{
  assert.deepEqual(withoutManagedFields([
    {key:'PORT',value:'9000'},
    {key:'FRONTEND_ORIGIN',value:'https://wrong.example'},
    {key:'CUSTOM_SETTING',value:'ok'},
  ],'backend'), [{key:'CUSTOM_SETTING',value:'ok'}])
  assert.deepEqual(withoutManagedFields([
    {key:'NEXT_PUBLIC_API_URL',value:'https://wrong.example/api'},
    {key:'NEXT_PUBLIC_TITLE',value:'Mon application'},
  ],'frontend'), [{key:'NEXT_PUBLIC_TITLE',value:'Mon application'}])
  const page=source('pages/StackDeploy.jsx')
  assert.match(page,/api_env_key:frontApiKey/)
  assert.match(page,/origin_env_key:backOriginKey/)
  assert.match(page,/validFrontendApiKey/)
  assert.match(page,/validBackendOriginKey/)
})
