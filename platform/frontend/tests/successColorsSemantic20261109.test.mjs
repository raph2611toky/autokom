import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const load = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const tokens = load('../src/styles/tokens.css')
const landing = load('../src/styles/landing.css')
const global = load('../src/styles/main.css')
const clipboard = load('../src/styles/copy-feedback.css')
const rule = (text, selector) => {
  const rules = [...text.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  const matching = rules.filter(([, heads]) => heads.split(',').some(head => head.trim() === selector))
  assert.ok(matching.length, `CSS absent : ${selector}`)
  return matching.at(-1)[2]
}
test('les deux thèmes définissent un vert de succès et son fond associé', () => {
  assert.match(tokens, /--ak-success:\s*#[\da-fA-F]+/g)
  assert.equal((tokens.match(/--ak-success-soft:\s*#[\da-fA-F]+/g) || []).length, 2)
})
test('le formulaire utilise fond, texte et bordure verts pour un succès', () => {
  const css = rule(landing, '.lp-contact-feedback-success')
  assert.match(css, /color:\s*var\(--ak-success\)/)
  assert.match(css, /background:\s*var\(--ak-success-soft\)/)
  assert.match(css, /border-left-color:\s*var\(--ak-success\)/)
})
test('les alertes communes succès, erreur, information conservent leurs couleurs sémantiques', () => {
  assert.match(rule(global, '.feedback.success'), /background:\s*var\(--ak-success-soft\)/)
  assert.match(rule(global, '.feedback.error'), /background:\s*var\(--ak-danger-soft\)/)
  assert.match(rule(global, '.feedback.info'), /background:\s*var\(--ak-accent-soft\)/)
  assert.match(rule(landing, '.lp-contact-feedback-error'), /background:\s*var\(--ak-danger-soft\)/)
})
test('les boutons de copie ne passent au vert que dans leur état de succès', () => {
  const css = rule(clipboard, '.ak-copy-action.ak-copy-success')
  assert.match(css, /color:\s*var\(--ak-success\)/)
  assert.match(css, /background:\s*var\(--ak-success-soft\)/)
  assert.match(rule(global, '.health-badge.good'), /background:\s*var\(--ak-success-soft\)/)
})
