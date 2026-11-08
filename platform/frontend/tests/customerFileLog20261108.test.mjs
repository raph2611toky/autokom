import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { technicalTimeline, formatTechnicalFileLine } from '../src/services/deploymentLogTimeline.js'

test('fichier technique : horodatage ISO, niveau, origine et message issu du backend', () => {
  const row={sequence:21,created_at:'2026-11-08T19:31:20Z',level:'DETAIL',
    message:'AUTOKOM_TRACE:release:application:sqlite3.OperationalError: unable to open database file'}
  const [line]=technicalTimeline([row])
  const formatted=formatTechnicalFileLine(line)
  assert.match(formatted.text,/^2026-11-08 19:31:20Z \[ERROR\] \[release\/application\] sqlite3\.OperationalError/)
  assert.equal(formatted.severity,'ERROR')
})

test('fichier technique : aucune interpolation HTML des messages', () => {
  const line={created_at:'2026-11-08T19:31:20Z',level:'DETAIL',stage_key:'release',channel:'broker', display:'<script>alert(1)</script>'}
  assert.match(formatTechnicalFileLine(line).text,/<script>/)
  const view=readFileSync(new URL('../src/pages/LiveRuns.jsx',import.meta.url),'utf8')
  assert.match(view,/<code>\{formatTechnicalFileLine\(row\)\.text\}<\/code>/)
  assert.doesNotMatch(view,/dangerouslySetInnerHTML/)
})

test('fichier technique : étapes normales restent informatives et non trompeuses', () => {
  const row={created_at:'2026-11-08T19:31:20Z',level:'INFO',stage_key:'build',display:'Préparation terminée'}
  assert.match(formatTechnicalFileLine(row).text,/\[INFO\] \[build\/autokom\] Préparation terminée/)
})

test('Synthèse conserve la grille existante, Technique adopte un affichage de fichier', () => {
  const view=readFileSync(new URL('../src/pages/LiveRuns.jsx',import.meta.url),'utf8')
  assert.match(view,/logMode === 'technical'/)
  assert.match(view,/ak-log-file-line/)
  assert.match(view,/ak-log-console-line/)
  assert.match(view,/useState\('simple'\)/)
})

test('réjection des marqueurs techniques arbitraires inchangée', () => {
  assert.deepEqual(technicalTimeline([{sequence:1,level:'DETAIL',message:'TOKEN=some-secret'}]),[])
})
