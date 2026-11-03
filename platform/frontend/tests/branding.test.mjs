import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

const landing = readFileSync(new URL('../src/pages/Landing.jsx', import.meta.url), 'utf8')
const tokens = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8')
const main = readFileSync(new URL('../src/styles/main.css', import.meta.url), 'utf8')

test('quatre logos pointent uniquement vers les domaines des solutions fournis', () => {
  const links = {
    diakom: 'https://dia.ik0m.tech',
    etakalo: 'https://etakalo.me',
    safidiko: 'https://safidiko.ik0m.tech',
    agriconnect: 'https://agriconnect.ik0m.tech',
  }
  for (const [name, url] of Object.entries(links)) {
    assert.ok(landing.includes(url), `Lien ${name} manquant`)
    assert.ok(landing.includes(`/solutions/${name}.webp`), `Image ${name} manquante`)
    assert.ok(existsSync(new URL(`../public/solutions/${name}.webp`, import.meta.url)), `Fichier ${name} introuvable`)
  }
  assert.match(landing, /rel="noopener noreferrer"/)
  assert.match(landing, /aria-label=\{`Ouvrir le site/)
})

test('onglets et boutons carrés, champs restaurés à 8 px', () => {
  assert.match(tokens, /--ak-control-radius:\s*0px/)
  for (const klass of ['.tab-button', '.button-primary', '.step-tab']) {
    const idx = main.indexOf(klass + ' {')
    assert.ok(idx >= 0, `Sélecteur manquant ${klass}`)
    assert.match(main.slice(idx, main.indexOf('}',idx)), /border-radius:\s*var\(--ak-control-radius\)/)
  }
  const klass = '.field input, .field select, .field textarea'
  const idx = main.indexOf(klass + ' {')
  assert.ok(idx >= 0, `Sélecteur manquant ${klass}`)
  assert.match(main.slice(idx, main.indexOf('}', idx)), /border-radius:\s*8px/)
})
