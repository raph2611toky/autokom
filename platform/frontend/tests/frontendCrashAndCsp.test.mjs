import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const file = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('la fiche projet référence explicitement la fonction URL publique', () => {
  const source = file('../src/pages/LiveProjects.jsx')
  assert.match(source, /import\s*\{\s*publicSiteUrl\s*\}\s*from\s*['"]\.\.\/services\/sitePresentation\.js['"]/) 
  assert.match(source, /publicSiteUrl\(detail\.project\.public_site\)/)
})

test('aucun script inline au démarrage sous CSP script-src self', () => {
  const html = file('../index.html')
  const external = file('../public/theme-init.js')
  assert.match(html, /<script src="\/theme-init\.js"><\/script>/)
  assert.doesNotMatch(html, /<script(?:\s[^>]*)?>(?!\s*<\/script>)[\s\S]*?<\/script>/i)
  assert.match(external, /autokom-theme/)
  assert.match(external, /prefers-color-scheme/)
  assert.doesNotMatch(external, /eval\s*\(|new Function\s*\(/)
})

test('une exception React affiche une page de récupération plutôt qu’un écran vide', () => {
  const main = file('../src/main.jsx')
  const boundary = file('../src/components/AppErrorBoundary.jsx')
  assert.match(main, /import AppErrorBoundary from/)
  assert.match(main, /<AppErrorBoundary><AuthProvider><App \/><\/AuthProvider><\/AppErrorBoundary>/)
  assert.match(boundary, /getDerivedStateFromError/)
  assert.match(boundary, /Votre projet reste enregistré/)
  assert.match(boundary, /window\.location\.reload\(\)/)
  assert.doesNotMatch(boundary, /error\.stack/)
})
