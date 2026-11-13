import { useState } from 'react'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText } from '../services/projectWorkflow.js'

const labels = {
  COMPLETE: 'Inférence locale terminée',
  UNAVAILABLE: 'Modèle local indisponible',
  DISABLED: 'Analyse IA non configurée',
  NO_CANDIDATE: 'Aucun extrait compatible à analyser',
}

export default function SecurityRunReport({ run, projectId, environmentId }) {
  const [target, setTarget] = useState('')
  const [channel, setChannel] = useState('ISSUE')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const report = run.sast_report
  if (!report || typeof report.count !== 'number') return null
  const terminal = ['FAILED', 'SUCCEEDED', 'BLOCKED'].includes(run.status)
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setNotice('')
    try {
      const result = await deploymentsApi.securityFeedback(projectId, environmentId, run.id, channel, channel === 'PR' ? Number(target) : 0)
      setNotice(result?.already_sent ? 'Ce résumé a déjà été envoyé.' : 'Résumé envoyé.')
    } catch (error) { setNotice(errorText(error)) }
    finally { setBusy(false) }
  }
  return <section className="ak-workspace-sheet ak-run-detail-sheet" aria-labelledby="run-sast-title">
    <div className="ak-workspace-sheet-heading"><div><h2 id="run-sast-title">Rapport SAST</h2>
      <p>{report.count} résultat(s) Semgrep · DeepSAST : {labels[report.ai?.status] || 'État inconnu'}</p>
      {report.ai?.status === 'COMPLETE' && <p><strong>{report.ai.analyzed}</strong> extrait(s) analysé(s) par le modèle local · {report.ai.scope === 'SOURCE' ? 'Échantillon de la source' : 'Emplacements signalés par Semgrep'}</p>}
      <p>Résultat consultatif. Semgrep conserve la décision sur le code.</p>
    </div></div>
    {Object.entries(report.rules || {}).length > 0 && <details><summary>Règles Semgrep signalées</summary>
      <ul>{Object.entries(report.rules).map(([rule, count]) => <li key={rule}>{rule} · {count}</li>)}</ul></details>}
    {terminal && !run.security_feedback_enabled && report.count > 0 && <p>Envoi externe désactivé sur cette instance.</p>}
    {terminal && run.security_feedback_enabled && report.count > 0 && <form onSubmit={submit}>
      <p>Envoyer un résumé sans extrait de code ni chemin privé.</p>
      <label className="field"><span>Destination</span><select value={channel} onChange={e => setChannel(e.target.value)}>
        <option value="ISSUE">Nouvelle issue dans le dépôt</option>
        <option value="PR">Commentaire sur une pull request existante</option>
        <option value="EMAIL">Mon adresse vérifiée</option>
      </select></label>
      {channel === 'PR' && <label className="field"><span>Numéro de la pull request</span>
        <input type="number" value={target} onChange={e => setTarget(e.target.value)} min="1" max="2147483647" step="1" required/></label>}
      <button type="submit" className="button-outline" disabled={busy}>{busy ? 'Envoi…' : 'Envoyer le résumé'}</button>
      {notice && <p role="status">{notice}</p>}
    </form>}
  </section>
}
