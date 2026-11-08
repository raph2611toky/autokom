import { useEffect, useRef, useState } from 'react'
import {
  ArrowDown, ArrowUpRight, Boxes, Check,
  CircleDot, CloudUpload, Code2, Cpu,
  GitBranch, Globe2, Layers3, LockKeyhole, Menu, MonitorDot, 
  ScanSearch, Server, ShieldCheck, Sparkles,
  TerminalSquare, Workflow, X,
} from 'lucide-react'
import ThemeToggle from '../components/ThemeToggle.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import ContactSection from './ContactSection.jsx'
import { PRICING_PLANS, formatAr, rememberPlan } from '../config/pricing.js'
import { AUTOKOM_MARK_OUTLINE } from '../config/brandOutline.js'
import '../styles/landing.css'

const workflow = [
  { number: '01', icon: GitBranch, title: 'Connectez votre code', description: 'Associez un dépôt Git et choisissez la branche ou le commit à déployer.' },
  { number: '02', icon: Boxes, title: 'Préparez le projet', description: 'Définissez le build, le runtime, les variables et les ressources nécessaires.' },
  { number: '03', icon: ShieldCheck, title: 'Choisissez votre pipeline', description: 'Activez un parcours DevOps ou DevSecOps dans les limites fixées par l’administrateur.' },
  { number: '04', icon: Globe2, title: 'Publiez et supervisez', description: 'Suivez chaque étape, associez un domaine et surveillez votre application.' },
]

const features = [
  { icon: Workflow, tag: 'Pipeline', title: 'Chaque étape. En toute transparence.', text: 'Suivez les stages réels, les journaux et les rapports de chaque exécution, du commit à la publication.', big: true },
  { icon: ShieldCheck, tag: 'Sécurité', title: 'Une sécurité qui s’adapte.', text: 'Sélectionnez DevOps ou DevSecOps pour vos applications, selon les règles et outils disponibles.' },
  { icon: Globe2, tag: 'Publication', title: 'Votre application, votre adresse.', text: 'Sous-domaine AUTOKOM ou domaine personnalisé, avec validation DNS et HTTPS.' },
  { icon: Cpu, tag: 'Ressources', title: 'Des limites maîtrisées.', text: 'Configurez CPU, RAM, stockage et instances, avec contrôle des quotas avant déploiement.' },
  { icon: Sparkles, tag: 'Assistance IA', title: 'L’IA comme copilote.', text: 'Obtenez de l’aide pour configurer votre projet ou comprendre un échec, sans décision imposée.' },
]

const stages = {
  devops: [
    { icon: GitBranch, title: 'Source', note: 'Récupération du code' },
    { icon: Code2, title: 'Build & tests', note: 'Construction et validation' },
    { icon: CloudUpload, title: 'Publication', note: 'Mise en service' },
    { icon: MonitorDot, title: 'Healthcheck', note: 'Contrôle de disponibilité' },
  ],
  devsecops: [
    { icon: GitBranch, title: 'Source', note: 'Récupération du code' },
    { icon: Code2, title: 'Build & tests', note: 'Construction et validation' },
    { icon: ScanSearch, title: 'Analyses sécurité', note: 'SAST, dépendances, images*' },
    { icon: CloudUpload, title: 'Publication', note: 'Mise en service' },
    { icon: MonitorDot, title: 'Healthcheck', note: 'Contrôle de disponibilité' },
  ],
}


// Logos fournis par IKOM : aucune fiche ou statistique n'est générée autour des liens.
const deployedSolutions = [
  { name: 'DiaKom', url: 'https://dia.ik0m.tech', logo: '/solutions/diakom.webp' },
  { name: 'Etakalo', url: 'https://etakalo.me', logo: '/solutions/etakalo.webp' },
  { name: 'Safidiko', url: 'https://safidiko.ik0m.tech', logo: '/solutions/safidiko.webp' },
  { name: 'AgriConnect', url: 'https://agriconnect.ik0m.tech', logo: '/solutions/agriconnect.webp' },
]

function introInitiallyVisible() {
  if (typeof window === 'undefined') return false
  // La visite précédente ne doit jamais masquer le loader : il se rejoue à chaque ouverture.
  const previewRequested = new URLSearchParams(window.location.search).has('previewLoader')
  return previewRequested || !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function previewForced() {
  return typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('previewLoader')
}

function LandingIntro({ leaving, dismiss, forceMotion }) {
  return (
    <div className={`lp-intro-overlay ${leaving ? 'is-leaving' : ''} ${forceMotion ? 'is-forced' : ''}`} role="status" aria-label="Ouverture d’AUTOKOM" aria-live="polite">
      <div className="lp-intro-center">
        <div className="lp-intro-mark" aria-hidden="true">
          <svg className="lp-intro-outline" viewBox="0 0 512 488" aria-hidden="true" focusable="false">
            <path d={AUTOKOM_MARK_OUTLINE} pathLength="1000" />
          </svg>
          <img className="lp-intro-contour" src="/brand/intro-contour.webp" alt="" width="512" height="488" fetchPriority="high" />
          <img className="lp-intro-k" src="/brand/intro-k.webp" alt="" width="512" height="488" fetchPriority="high" />
          <img className="lp-intro-accent" src="/brand/intro-accent.webp" alt="" width="512" height="488" />
          <img src="/autokom-logo.png" className="lp-intro-complete" alt="" width="512" height="488" />
        </div>
        <strong className="lp-intro-word" aria-label="AUTOKOM">
          {Array.from('AUTOKOM').map((letter, index) => (
            <span key={`${letter}-${index}`} className="lp-intro-letter" style={{ '--letter-order': index }} aria-hidden="true">{letter}</span>
          ))}
        </strong>
        <span className="lp-intro-caption">Votre espace de déploiement</span>
        <div className="lp-intro-progress" aria-hidden="true"><span /></div>
      </div>
      <button type="button" className="lp-intro-skip" onClick={dismiss}>Passer</button>
    </div>
  )
}

function SolutionLogo({ name, url, logo }) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  return (
    <a className={`lp-solution-link lp-solution-${name.toLowerCase()}`} href={url} target="_blank" rel="noopener noreferrer" title={`Visiter ${name}`} aria-label={`Ouvrir le site ${name} dans un nouvel onglet`}>
      {!loaded && !failed && <span className="lp-logo-skeleton" aria-hidden="true" />}
      <img src={logo} alt={`Logo ${name}`} loading="lazy" decoding="async" className={loaded || failed ? 'is-loaded' : 'is-loading'} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
    </a>
  )
}

function Brand({ navigate }) {
  return (
    <button type="button" className="lp-brand" onClick={() => navigate('/')} aria-label="AUTOKOM, retour à l’accueil">
      <img src="/autokom-logo.png" alt="" />
      <span>AUTOKOM<span className="lp-brand-suffix"> / PLATFORM</span></span>
    </button>
  )
}

function LandingHeader({ navigate, theme, onToggleTheme, introBusy = false, sessionStatus = 'anonymous' }) {
  const [open, setOpen] = useState(false)
  const connected = sessionStatus === 'authenticated'
  const checking = sessionStatus === 'loading'
  const close = () => setOpen(false)
  const links = [
    ['#fonctionnement', 'Comment ça marche'],
    ['#fonctionnalites', 'Fonctionnalités'],
    ['#tarifs', 'Tarifs'],
    ['#securite', 'Sécurité'],
    ['#contact', 'Nous contacter'],
  ]

  return (
    <header className="lp-header" inert={introBusy}>
      <div className="lp-container lp-nav">
        <Brand navigate={navigate} />
        <nav id="lp-navigation" className={`lp-nav-links ${open ? 'is-open' : ''}`} aria-label="Navigation de l’accueil">
          {links.map(([href, label]) => <a key={href} href={href} onClick={close}>{label}</a>)}
          <button type="button" className="lp-nav-mobile-cta" disabled={checking} onClick={() => { close(); navigate(connected ? '/dashboard' : '/register') }}>{checking ? 'Vérification…' : connected ? 'Mon espace' : 'Créer un compte'} <ArrowUpRight size={16} /></button>
          <button type="button" className="lp-nav-mobile-login" disabled={checking} onClick={() => { close(); navigate(connected ? '/projects' : '/login') }}>{connected ? 'Mes projets' : 'Se connecter'}</button>
        </nav>
        <div className="lp-nav-actions">
          <ThemeToggle theme={theme} onToggle={onToggleTheme} className="lp-theme-toggle" />
          <button type="button" className={`lp-nav-login ${connected ? 'lp-nav-login-connected' : ''}`} disabled={checking} onClick={() => navigate(connected ? '/dashboard' : '/login')} aria-label={connected ? 'Ouvrir mon espace AUTOKOM' : 'Se connecter à AUTOKOM'}>{checking ? 'Vérification…' : connected ? 'Mon espace' : 'Se connecter'} <ArrowUpRight size={16} /></button>
          <button type="button" className="lp-mobile-menu" aria-label={open ? 'Fermer le menu' : 'Ouvrir le menu'} aria-controls="lp-navigation" aria-expanded={open} onClick={() => setOpen(value => !value)}>
            {open ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>
    </header>
  )
}

function PipelinePreview({ mode, setMode, ready }) {
  const [switching, setSwitching] = useState(false)
  const switchTimer = useRef(null)

  useEffect(() => () => window.clearTimeout(switchTimer.current), [])

  function chooseMode(nextMode) {
    if (nextMode === mode || switching) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setMode(nextMode)
      return
    }
    setSwitching(true)
    switchTimer.current = window.setTimeout(() => {
      setMode(nextMode)
      setSwitching(false)
    }, 250)
  }

  return (
    <div className={`lp-preview ${ready ? 'is-stage-ready' : ''}`} aria-label="Fonctionnement des pipelines AUTOKOM">
      <div className="lp-preview-topbar">
        <span className="lp-preview-dots" aria-hidden="true"><i /><i /><i /></span>
        <span>autokom / pipeline</span>
        <span className="lp-preview-kebab" aria-hidden="true">•••</span>
      </div>
      <div className="lp-preview-content">
        <div className="lp-preview-overline"><span className="lp-preview-purple-point" /> PIPELINE AUTOKOM</div>
        <div className="lp-preview-title-line">
          <div>
            <span className="lp-preview-caption">Votre déploiement, étape par étape</span>
            <h3>Visualisation du pipeline</h3>
          </div>
          <span className="lp-preview-symbol"><Workflow size={22}/></span>
        </div>
        <div className="lp-preview-mode" role="group" aria-label="Mode de pipeline présenté">
          <button type="button" onClick={() => chooseMode('devops')} className={mode === 'devops' ? 'selected' : ''} aria-pressed={mode === 'devops'}>DevOps</button>
          <button type="button" onClick={() => chooseMode('devsecops')} className={mode === 'devsecops' ? 'selected' : ''} aria-pressed={mode === 'devsecops'}><ShieldCheck size={14}/> DevSecOps</button>
        </div>
        <div key={`${mode}-${switching ? 'loading' : 'ready'}`} className="lp-preview-stage-list" aria-busy={switching}>
          {switching ? Array.from({ length: stages[mode].length }, (_, index) => (
            <div key={index} className="lp-preview-skeleton-stage" aria-hidden="true">
              <span className="lp-preview-skeleton-icon" />
              <span className="lp-preview-skeleton-copy"><i /><i /></span>
            </div>
          )) : stages[mode].map(({ title, note, icon: Icon }, index) => (
            <div className="lp-preview-stage" key={title} style={{ '--lp-stage-order': index }}>
              <div className="lp-stage-icon"><Icon size={17} /></div>
              <div className="lp-stage-copy"><strong>{title}</strong><span>{note}</span></div>
              <span className="lp-stage-counter">{String(index + 1).padStart(2, '0')}</span>
            </div>
          ))}
        </div>
        <div className="lp-preview-bottom"><CircleDot size={14} /> Stages, rapports et journaux disponibles pour chaque déploiement.</div>
      </div>
      <div className="lp-preview-floating lp-floating-top"><span className="lp-floating-icon"><LockKeyhole size={17}/></span><span>Contrôles de sécurité<br/><strong>intégrés au parcours</strong></span></div>
      <div className="lp-preview-floating lp-floating-bottom"><span className="lp-floating-icon lp-floating-blue"><Globe2 size={18}/></span><span>Git → Pipeline → HTTPS<br/><strong>Un seul espace de travail</strong></span></div>
    </div>
  )
}

export default function Landing({ navigate, theme, onToggleTheme }) {
  const { status: sessionStatus } = useAuth()
  const connected = sessionStatus === 'authenticated'
  const checkingSession = sessionStatus === 'loading'
  const [mode, setMode] = useState('devsecops')
  const [introState, setIntroState] = useState(() => introInitiallyVisible() ? 'loading' : 'done')
  const [forceIntroMotion, setForceIntroMotion] = useState(previewForced)
  const rootRef = useRef(null)

  function finishIntro() {
    setIntroState('done')
    setForceIntroMotion(false)
  }

  function replayIntro() {
    // Un clic explicite peut rejouer le logo même si le système réduit les animations.
    setForceIntroMotion(true)
    setIntroState('loading')
    window.scrollTo({ top: 0, behavior: 'instant' })
  }

  useEffect(() => {
    if (introState === 'done') return
    const timer = window.setTimeout(() => {
      if (introState === 'loading') setIntroState('leaving')
      else finishIntro()
    }, introState === 'loading' ? 3100 : 360)
    return () => window.clearTimeout(timer)
  }, [introState])

  useEffect(() => {
    const root = rootRef.current
    if (!root || introState !== 'done') return
    if (!('IntersectionObserver' in window) || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const elements = [...root.querySelectorAll('[data-lp-reveal]')]
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target) } })
    }, { threshold: 0.08, rootMargin: '0px 0px -24px 0px' })
    root.classList.add('lp-motion-ready')
    elements.forEach(element => observer.observe(element))
    return () => { observer.disconnect(); root.classList.remove('lp-motion-ready') }
  }, [introState])

  return (
    <div className={`lp ${forceIntroMotion ? 'lp-force-intro' : ''}`} data-theme={theme} ref={rootRef}>
      {introState !== 'done' && <LandingIntro leaving={introState === 'leaving'} dismiss={finishIntro} forceMotion={forceIntroMotion} />}
      <a href="#lp-main" className="lp-skip-link" tabIndex={introState !== 'done' ? -1 : undefined}>Aller au contenu</a>
      <LandingHeader navigate={navigate} theme={theme} onToggleTheme={onToggleTheme} introBusy={introState !== 'done'} sessionStatus={sessionStatus}/>

      <main id="lp-main" inert={introState !== 'done'}>
        <section className="lp-hero" aria-labelledby="lp-title">
          <div className="lp-hero-glow" aria-hidden="true" />
          <div className="lp-container lp-hero-inner">
            <div className="lp-hero-text">
              <div className="lp-eyebrow"><span className="lp-eyebrow-dot" /> PLATEFORME DE DÉPLOIEMENT INTELLIGENTE </div>
              <h1 id="lp-title"><span className="lp-hero-line">Du code.</span><span className="lp-hero-line">Au cloud.</span><span className="lp-hero-line lp-hero-line-accent">En confiance<span className="lp-title-period">.</span></span></h1>
              <p className="lp-hero-lead">Connectez votre projet, configurez votre pipeline <strong>DevOps ou DevSecOps</strong> et suivez votre application jusqu’à sa mise en ligne. Tout au même endroit.</p>
              <div className="lp-hero-actions">
                <button className="lp-cta-primary" type="button" disabled={checkingSession} onClick={() => navigate(connected ? '/dashboard' : '/register')}>{checkingSession ? 'Vérification de la session…' : connected ? 'Accéder à mon espace' : 'Créer mon espace'} <ArrowUpRight size={19}/></button>
                <a className="lp-cta-outline" href="#fonctionnement">Découvrir le fonctionnement <ArrowDown size={18}/></a>
              </div>
              {connected && <p className="lp-session-active" role="status"><span className="lp-session-dot" aria-hidden="true" /> Vous êtes déjà connecté à AUTOKOM.</p>}
              <div className="lp-hero-footnote"><div className="lp-hero-footnote-stars" aria-hidden="true">✳</div><span>De la première configuration<br/>à la supervision de vos applications.</span></div>
            </div>
            <div className="lp-hero-art" data-lp-reveal style={{ '--lp-reveal-delay': '180ms' }}>
              <div className="lp-art-halo lp-art-halo-one" aria-hidden="true" />
              <div className="lp-art-halo lp-art-halo-two" aria-hidden="true" />
              <div className="lp-art-decor lp-art-decor-one" aria-hidden="true">+</div>
              <div className="lp-art-decor lp-art-decor-two" aria-hidden="true">✳</div>
              <PipelinePreview mode={mode} setMode={setMode} ready={introState === 'done'}/>
            </div>
          </div>
          <div className="lp-container lp-hero-bottom"><span>DÉCOUVRIR AUTOKOM</span><a href="#fonctionnement" aria-label="Défiler vers le fonctionnement"><ArrowDown size={20}/></a><span>DES OUTILS, UN PARCOURS CLAIR</span></div>
        </section>

        <div className="lp-trust-bar" aria-label="Principes de la plateforme">
          <div className="lp-container lp-trust-inner"><span><GitBranch size={18}/> CODE SOURCE</span><i/> <span><Workflow size={19}/> PIPELINES VISIBLES</span><i/> <span><ShieldCheck size={19}/> SÉCURITÉ MAÎTRISÉE</span><i/> <span><Globe2 size={18}/> PUBLICATION HTTPS</span></div>
        </div>

        <section className="lp-section lp-how" id="fonctionnement" aria-labelledby="lp-how-title">
          <div className="lp-container">
            <div className="lp-section-intro lp-intro-split" data-lp-reveal>
              <div><span className="lp-kicker">COMMENT ÇA MARCHE</span><h2 id="lp-how-title">Quatre étapes.<br/><em>Une seule plateforme.</em></h2></div>
              <p>AUTOKOM simplifie le parcours du développeur sans cacher ce qui se passe réellement lors du déploiement.</p>
            </div>
            <div className="lp-steps">
              {workflow.map(({ number, icon: Icon, title, description }, index) => (
                <article key={number} className="lp-step" data-lp-reveal style={{ '--lp-reveal-delay': `${index * 95}ms` }}>
                  <div className="lp-step-top"><Icon size={26} strokeWidth={1.7}/></div>
                  <h3>{title}</h3><p>{description}</p>
                  <span className="lp-step-arrow"><ArrowUpRight size={20}/></span>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-features" id="fonctionnalites" aria-labelledby="lp-features-title">
          <div className="lp-container">
            <div className="lp-section-intro lp-center-intro" data-lp-reveal>
              <span className="lp-kicker">UNE PLATEFORME, TOUT LE PARCOURS</span>
              <h2 id="lp-features-title">Conçu pour déployer.<br/><em>Pensé pour garder le contrôle.</em></h2>
              <p>De la préparation du projet à son suivi en production, retrouvez les informations essentielles au bon endroit.</p>
            </div>
            <div className="lp-features-grid">
              {features.map(({ icon: Icon, tag, title, text, big }, index) => (
                <article key={tag} className={`lp-feature ${big ? 'lp-feature-large' : ''}`} data-lp-reveal style={{ '--lp-reveal-delay': `${(index % 3) * 110}ms` }}>
                  <div className="lp-feature-head"><span>{tag}</span><Icon size={25} strokeWidth={1.8}/></div>
                  {big && <div className="lp-feature-large-visual" aria-hidden="true"><span>Source</span><i/><span>Build</span><i/><span>Analyse</span><i/><span>Publication</span></div>}
                  <div className="lp-feature-copy"><h3>{title}</h3><p>{text}</p></div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-pricing" id="tarifs" aria-labelledby="lp-pricing-title">
          <div className="lp-container">
            <div className="lp-section-intro lp-center-intro" data-lp-reveal>
              <span className="lp-kicker">NOS OFFRES</span>
              <h2 id="lp-pricing-title">Un projet, <em>une offre adaptée.</em></h2>
              <p>Commencez gratuitement. Choisissez ensuite les ressources adaptées à votre application.</p>
            </div>
            <div className="lp-price-grid lp-price-grid-three">
              {PRICING_PLANS.map((plan, index) => <article key={plan.id} className={`lp-price-card ${plan.highlighted ? 'lp-price-card-featured' : ''}`} data-lp-reveal style={{ '--lp-reveal-delay': `${index * 115}ms` }}>
                <div className="lp-price-card-head"><span>{plan.name}</span><span className="lp-price-label">{plan.id === 'free' ? 'Gratuit' : 'Par mois'}</span></div>
                <div className="lp-price-value">{formatAr(plan.monthly)}<small> / mois</small></div>
                <p>{plan.tagline}</p>
                <div className="lp-price-features">{[plan.servers, ...plan.benefits.slice(0,3)].map(benefit => <span key={benefit}><Check size={17}/>{benefit}</span>)}</div>
                <button type="button" className={`lp-price-button ${plan.highlighted ? 'lp-price-button-primary' : ''}`} onClick={() => { rememberPlan(plan.id); navigate('/deploy') }}>{plan.cta}<ArrowUpRight size={18}/></button>
              </article>)}
            </div>
            <div className="lp-pricing-more"><button type="button" onClick={() => navigate('/pricing')}>Voir les tarifs en détail <ArrowUpRight size={16}/></button></div>
            <p className="lp-price-fine">Tarifs proposés par IKOM, en cours de validation. Les ressources et les montants seront confirmés avant commercialisation.</p>
          </div>
        </section>

        <section className="lp-section lp-security" id="securite" aria-labelledby="lp-security-title">
          <div className="lp-container lp-security-layout">
            <div className="lp-security-copy" data-lp-reveal>
              <span className="lp-kicker lp-kicker-light">LA SÉCURITÉ AU BON ENDROIT</span>
              <h2 id="lp-security-title">Le choix pour vos apps.<br/><em>La protection pour l’infra.</em></h2>
              <p>Vos applications peuvent utiliser un pipeline DevOps ou DevSecOps. L’infrastructure qui exécute AUTOKOM, elle, reste systématiquement DevSecOps.</p>
              <a href="#fonctionnalites" className="lp-security-link">Explorer les fonctionnalités <ArrowUpRight size={17}/></a>
            </div>
            <div className="lp-security-cards" data-lp-reveal>
              <div className="lp-security-card"><div className="lp-security-card-top"><Server size={24}/><span>INFRASTRUCTURE AUTOKOM</span></div><strong>DevSecOps, toujours.</strong><p>Les outils et politiques sont définis par l’administrateur de la plateforme.</p><span className="lp-security-chip"><Check size={14}/> SOCLE NON DÉSACTIVABLE</span></div>
              <div className="lp-security-card lp-security-card-soft"><div className="lp-security-card-top"><TerminalSquare size={24}/><span>APPLICATIONS CLIENTS</span></div><strong>Un pipeline à votre mesure.</strong><p>Choisissez DevOps ou DevSecOps parmi les capacités autorisées.</p><span className="lp-security-chip lp-security-chip-purple"><Layers3 size={14}/> CHOIX PAR APPLICATION</span></div>
            </div>
          </div>
        </section>

        <section className="lp-section lp-detail" aria-labelledby="lp-detail-title">
          <div className="lp-container lp-detail-layout">
            <div className="lp-detail-visual" data-lp-reveal>
              <div className="lp-detail-visual-label"><span className="lp-orange-dot"/> DE LA SOURCE À LA PUBLICATION</div>
              <div className="lp-detail-flow">
                {[{ icon: GitBranch, title: 'Dépôt Git', caption: 'Source de vérité' }, { icon: Workflow, title: 'Pipeline CI/CD', caption: 'Étapes tracées' }, { icon: Globe2, title: 'Site publié', caption: 'Domaine & HTTPS' }].map(({ icon: Icon, title, caption }, index) => (
                  <div key={title} className="lp-flow-step"><div className="lp-flow-symbol"><Icon size={23}/></div><div><strong>{title}</strong><span>{caption}</span></div>{index < 2 && <div className="lp-flow-line" aria-hidden="true"/>}</div>
                ))}
              </div>
              <div className="lp-detail-preview-note"><CircleDot size={15}/> Du dépôt à la mise en ligne</div>
            </div>
            <div className="lp-detail-copy" data-lp-reveal>
              <span className="lp-kicker">VISIBILITÉ DE BOUT EN BOUT</span>
              <h2 id="lp-detail-title">Moins de complexité.<br/><em>Plus de visibilité.</em></h2>
              <p>Un déploiement ne se résume pas à un bouton. AUTOKOM rassemble les décisions, les étapes techniques, les résultats et les opérations de suivi dans un seul espace.</p>
              <div className="lp-detail-benefits">
                <div><Check size={16}/><span>Configuration centralisée et versionnée</span></div>
                <div><Check size={16}/><span>Stages, logs et erreurs consultables</span></div>
                <div><Check size={16}/><span>Domaines, ressources et santé applicative</span></div>
              </div>
              <button type="button" className="lp-inline-link" onClick={() => navigate('/login')}>Accéder à la plateforme <ArrowUpRight size={18}/></button>
            </div>
          </div>
        </section>

        <ContactSection />

        <section className="lp-final" aria-labelledby="lp-final-title">
          <div className="lp-container lp-final-inner" data-lp-reveal>
            <span className="lp-kicker">PRÊT À DÉPLOYER AUTREMENT ?</span>
            <h2 id="lp-final-title">Votre prochain projet<br/><em>commence ici.</em></h2>
            <p>Une interface pour construire, déployer et garder un œil sur vos applications.</p>
            <button type="button" className="lp-cta-primary lp-final-button" onClick={() => navigate('/register')}>Créer mon espace <ArrowUpRight size={19}/></button>
            <div className="lp-final-mark" aria-hidden="true">A<span>✳</span></div>
          </div>
        </section>

        <section className="lp-solutions" aria-labelledby="lp-solutions-title">
          <div className="lp-container">
            <h2 id="lp-solutions-title" className="lp-solutions-title" data-lp-reveal>
              Des solutions qui utilisent AUTOKOM
            </h2>
          </div>
          <div className="lp-solutions-viewport" aria-label="Solutions qui utilisent AUTOKOM" data-lp-reveal>
            <div className="lp-solutions-track">
              <div className="lp-solutions-run">
                {deployedSolutions.map(({ name, url, logo }) => (
<SolutionLogo key={name} name={name} url={url} logo={logo} />
                ))}
              </div>
              {/* Copie visuelle pour un défilement continu, retirée de la navigation clavier. */}
              <div className="lp-solutions-run lp-solutions-clone" aria-hidden="true">
                {deployedSolutions.map(({ name, url, logo }) => (
                  <a
                    key={`${name}-clone`}
                    className={`lp-solution-link lp-solution-${name.toLowerCase()}`}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    tabIndex={-1}
                    aria-hidden="true"
                  >
                    <img src={logo} alt="" loading="lazy" decoding="async" />
                  </a>
                ))}
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer" inert={introState !== 'done'}>
        <div className="lp-container lp-footer-main">
          <div className="lp-footer-brand"><Brand navigate={navigate}/><p>De l’idée à la mise en ligne, un parcours de déploiement plus clair.</p></div>
          <div className="lp-footer-links"><span>EXPLORER</span><a href="#fonctionnement">Fonctionnement</a><a href="#fonctionnalites">Fonctionnalités</a><a href="#tarifs">Tarifs</a><a href="#securite">Sécurité</a><a href="#contact">Nous contacter</a></div>
          <div className="lp-footer-links"><span>PLATEFORME</span><button type="button" onClick={() => navigate('/login')}>Connexion</button><button type="button" onClick={() => navigate('/register')}>Inscription</button><button type="button" onClick={() => navigate('/deploy')}>Configurer un projet</button><button type="button" onClick={replayIntro}>Revoir l’animation d’accueil</button></div>
        </div>
        <div className="lp-container lp-footer-bottom"><span>© {new Date().getFullYear()} AUTOKOM. Tous droits réservés.</span><span>PLATEFORME DE DÉPLOIEMENT • DEVOPS / DEVSECOPS</span></div>
      </footer>
    </div>
  )
}
