import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const jsx = readFileSync(new URL('../src/pages/Landing.jsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/styles/landing.css', import.meta.url), 'utf8')

test('loader AUTOKOM visible à chaque ouverture et rejouable', () => {
  assert.match(jsx, /function LandingIntro/)
  assert.match(jsx, /<img src="\/autokom-logo\.png"/)
  assert.doesNotMatch(jsx, /INTRO_SESSION_KEY|sessionStorage\.getItem/)
  assert.match(jsx, /return previewRequested \|\| !window\.matchMedia/)
  assert.match(jsx, /function replayIntro\(/)
  assert.match(jsx, /Revoir l’animation d’accueil/)
  assert.match(jsx, /onClick=\{dismiss\}>Passer/)
  assert.match(jsx, /window\.clearTimeout\(timer\)/)
  assert.match(jsx, /inert=\{introState !== 'done'\}/)
  assert.match(css, /\.lp-intro-overlay\.is-leaving/)
  assert.match(css, /\.lp-intro-overlay:not\(\.is-forced\)/)
})

test('apparition progressive des textes, cartes et contenus du pipeline', () => {
  assert.match(jsx, /lp-hero-line-accent/)
  assert.match(css, /lp-hero-line:nth-child\(3\)/)
  assert.match(css, /lp-type-rise/)
  assert.match(jsx, /--lp-reveal-delay/)
  assert.match(jsx, /--lp-stage-order/)
  assert.match(css, /lp-stage-enter/)
  assert.match(jsx, /lp-preview-skeleton-stage/)
  assert.match(css, /lp-preview-skeleton-copy/)
  assert.match(jsx, /aria-busy=\{switching\}/)
  assert.match(css, /lp-card-scan/)
})

test('les logos montrent un placeholder uniquement jusqu au chargement de leur image', () => {
  assert.match(jsx, /function SolutionLogo/)
  assert.match(jsx, /onLoad=\{\(\) => setLoaded\(true\)\}/)
  assert.match(jsx, /onError=\{\(\) => setFailed\(true\)\}/)
  assert.match(css, /lp-logo-skeleton/)
  assert.match(css, /lp-skeleton-shimmer/)
})

test('réduction des animations et aucune dépendance backend ajoutée', () => {
  assert.match(css, /prefers-reduced-motion: reduce/)
  assert.match(jsx, /prefers-reduced-motion: reduce/)
  assert.doesNotMatch(jsx, /authApi|currentAccount|django|axios|supabase/i)
})


test('loader trace le vrai contour du logo et révèle les éléments dans l’ordre', () => {
  assert.match(jsx, /AUTOKOM_MARK_OUTLINE/)
  assert.match(jsx, /lp-intro-outline/)
  assert.match(jsx, /intro-contour\.webp/)
  assert.match(jsx, /intro-k\.webp/)
  assert.match(jsx, /intro-accent\.webp/)
  assert.match(jsx, /Array\.from\('AUTOKOM'\)\.map/)
  assert.match(jsx, /lp-intro-letter/)
  assert.match(css, /lp-outline-draw/)
  assert.match(css, /lp-k-reveal/)
  assert.match(css, /lp-letter-reveal/)
  assert.match(css, /animation-delay: calc\(1\.54s \+ var\(--letter-order\) \* 95ms\)/)
  assert.match(jsx, /3100 : 360/)
  assert.match(jsx, /previewLoader/)
})
