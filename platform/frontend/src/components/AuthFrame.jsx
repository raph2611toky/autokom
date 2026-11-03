import { ArrowLeft, GitBranch, Globe2, ShieldCheck } from 'lucide-react'
import ThemeToggle from './ThemeToggle.jsx'

/** Structure unique des pages d'authentification.
 * Connexion : texte à gauche / formulaire à droite.
 * Inscription : formulaire à gauche / texte à droite.
 */
export default function AuthFrame({ kind, navigate, theme, onToggleTheme, title, subtitle, children }) {
  const isRegister = kind === 'register'
  return (
    <main className={`auth-ui auth-ui--${kind}`} id="main-content">
      <header className="auth-ui-top">
        <button type="button" className="auth-ui-home" onClick={() => navigate('/')} aria-label="Revenir à l'accueil AUTOKOM">
          <img src="/autokom-logo.png" alt="" />
          <strong>AUTOKOM</strong>
        </button>
        <div className="auth-ui-top-actions">
          <button type="button" className="auth-ui-back" onClick={() => navigate('/')}><ArrowLeft size={16} /> Accueil</button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </header>

      <div className="auth-ui-layout">
        <section className="auth-ui-intro" aria-label="Présentation de la plateforme">
          <div className="auth-ui-intro-content">
            <span className="auth-ui-accent-line" aria-hidden="true" />
            <h2>{isRegister ? <>Votre prochain projet<br /><em>commence ici.</em></> : <>Du code à la<br /><em>mise en ligne.</em></>}</h2>
            <p>{isRegister
              ? 'Créez votre espace, connectez vos dépôts et choisissez les outils adaptés à vos applications.'
              : 'Retrouvez vos projets, vos pipelines et vos applications depuis un même espace.'}</p>
            <div className="auth-ui-points" aria-label="Fonctionnalités principales">
              <span><GitBranch size={17} /> Dépôts Git</span>
              <span><ShieldCheck size={17} /> Pipelines maîtrisés</span>
              <span><Globe2 size={17} /> Publication HTTPS</span>
            </div>
          </div>
        </section>
        <section className="auth-ui-panel" aria-labelledby="auth-ui-title">
          <div className="auth-ui-panel-content">
            <div className="auth-ui-panel-heading">
              <h1 id="auth-ui-title">{title}</h1>
              <p>{subtitle}</p>
            </div>
            {children}
          </div>
        </section>
      </div>
    </main>
  )
}
