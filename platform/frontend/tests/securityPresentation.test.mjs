import test from 'node:test'
import assert from 'node:assert/strict'
import { githubRepositoryUrl, securityWarning } from '../src/services/securityPresentation.js'

test('only a validated repository slug can generate a GitHub link', () => {
  assert.equal(githubRepositoryUrl('Jennylady/portfolio'), 'https://github.com/Jennylady/portfolio')
  for (const value of ['../..','//other.tld','jane/repo?next=','jane/a/b',null]) assert.equal(githubRepositoryUrl(value), null)
})

test('only a successful publication claims the correction is live', () => {
  const run = {status:'FAILED', sca_report:{count:2, remediated:true}}
  assert.equal(securityWarning(run), 'Résultats de sécurité à examiner')
  assert.match(securityWarning({...run,status:'SUCCEEDED'}), /Correctifs appliqués/)
  assert.equal(securityWarning({status:'SUCCEEDED',sast_report:{count:0,ai:{status:'COMPLETE'}}}), '')
})
