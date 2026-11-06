import { Component } from 'react'

// Une erreur de rendu ne doit pas laisser l'espace AUTOKOM entièrement vide.
// Le composant ne journalise ni les détails d'erreur ni des secrets utilisateur.
export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="app-shell">
        <main className="container workspace" id="main-content" role="alert">
          <section className="ak-workspace-empty">
            <h1>Un problème d’affichage est survenu</h1>
            <p>Votre projet reste enregistré. Actualisez la page pour réessayer.</p>
            <div className="ak-project-detail-actions">
              <button className="button-primary" type="button" onClick={() => window.location.reload()}>Actualiser</button>
              <button className="button-outline" type="button" onClick={() => window.location.assign('/projects')}>Mes projets</button>
            </div>
          </section>
        </main>
      </div>
    )
  }
}
