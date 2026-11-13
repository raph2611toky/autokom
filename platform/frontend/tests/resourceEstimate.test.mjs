import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { estimateResources } from '../src/services/resourceEstimate.js'
import { detectSource } from '../src/services/sourceAnalysis.js'

const valid = { available: true }

test('l’estimation suit le profil sélectionné, y compris le choix export ou SSR', () => {
  const exportStatic = estimateResources([{ profile:'next_export', analysis:valid }])
  const server = estimateResources([{ profile:'next_server', analysis:valid }])
  assert.ok(server.total.cpu > exportStatic.total.cpu)
  assert.ok(server.total.ram > exportStatic.total.ram)
})

test('les dépendances de production du dépôt modulent le budget Node', () => {
  const files = [{type:'file', name:'package.json'}]
  const small = detectSource(files, {dependencies:{express:'x'}, devDependencies:{vite:'x'}})
  const large = detectSource(files, {dependencies:Object.fromEntries(Array.from({length:30}, (_,i)=>['dep'+i,'x']))})
  assert.equal(small.productionDependencyCount,1)
  assert.equal(large.productionDependencyCount,30)
  const build = analysis => estimateResources([{ profile:'node_express', analysis }]).total
  assert.ok(build(large).ram > build(small).ram)
})

test('le récapitulatif additionne les services et les répliques demandées', () => {
  const parts = [{profile:'vite',analysis:valid},{profile:'python_django',analysis:valid}]
  const one = estimateResources(parts)
  const two = estimateResources(parts, {highAvailability:true})
  assert.equal(one.services.length,2)
  assert.equal(two.total.ram,2*one.total.ram)
  assert.equal(two.replicas,2)
})

test('pas de chiffre présenté avant une analyse valide', () => {
  assert.equal(estimateResources([{profile:'vite',analysis:null}]),null)
  assert.equal(estimateResources([{profile:'unknown',analysis:valid}]),null)
})

test('les ressources recommandées utilisent une palette lisible en clair et en sombre', () => {
  const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
  const css = read('../src/styles/resource-estimate.css')
  const jsx = read('../src/components/ResourceEstimate.jsx')
  assert.match(jsx, /import '\.\.\/styles\/resource-estimate\.css'/)
  assert.doesNotMatch(jsx, /ak-stack-component/)
  assert.match(css, /background: var\(--ak-surface-2\)/)
  assert.match(css, /\.ak-resource-estimate \.ak-review-list strong\s*\{\s*color: var\(--ak-text-strong\)/)
  const palette = read('../src/styles/tokens.css').split(':root[data-theme="dark"]')
  const luminance = hex => hex.match(/../g).map((part, i) => {
    const n = Number.parseInt(part, 16) / 255
    return [0.2126, 0.7152, 0.0722][i] * (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4)
  }).reduce((sum, n) => sum + n, 0)
  for (const theme of palette) {
    const get = token => luminance(theme.match(new RegExp(`--ak-${token}: #([a-f0-9]{6});`))[1])
    const bg = get('surface-2')
    for (const token of ['text-strong', 'muted', 'success']) {
      const fg = get(token)
      assert.ok((Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05) >= 4.5, token)
    }
  }
})
