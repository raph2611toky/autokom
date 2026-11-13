import { useState } from 'react'
import { deploymentsApi } from '../services/deploymentsApi.js'
import { errorText } from '../services/projectWorkflow.js'
import { githubRepositoryUrl, securityWarning } from '../services/securityPresentation.js'

const labels = {
  COMPLETE: 'Inférence locale terminée',
  UNAVAILABLE: 'Modèle local indisponible',
  DISABLED: 'Analyse IA non configurée',
  NO_CANDIDATE: 'Aucun extrait compatible à analyser',
}

export default function SecurityRunReport({ run, projectId, environmentId, repository }) {
  const [target, setTarget] = useState('')
  const [channel, setChannel] = useState('ISSUE')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const report = typeof run.sast_report?.count === 'number' ? run.sast_report : null
  const sca = typeof run.sca_report?.count === 'number' ? run.sca_report : null
  if (!report && !sca) return null
  const terminal = ['FAILED', 'SUCCEEDED', 'BLOCKED'].includes(run.status)
  const warning = securityWarning(run)
  const issue = run.security_actions?.ISSUE
  const repoUrl = githubRepositoryUrl(repository)
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
  return <section className="ak-workspace-sheet ak-run-detail-sheet" aria-label="Rapport de sécurité">
    <div className="ak-workspace-sheet-heading"><div><h2>Rapport de sécurité</h2>
      {warning && <p className="ak-publication-pill state-waiting">{warning}</p>}
    </div></div>
    {sca && <div>
      <h3>Dépendances et secrets · Trivy</h3>
      <p>{sca.count} alerte(s) initiale(s) · {sca.remaining} vulnérabilité(s) restante(s) · {sca.secrets} secret(s)</p>
      {sca.remediated && <p>{run.status === 'SUCCEEDED' ? 'Le build publié utilise le lockfile corrigé et revérifié. Le dépôt GitHub n’a pas été modifié.' : 'Le lockfile candidat a passé la nouvelle analyse Trivy. La publication n’est pas encore confirmée.'}</p>}
      {sca.findings?.length > 0 && <details><summary>Voir les alertes et les versions correctives ({sca.findings.length})</summary>
        <ul>{sca.findings.map((item, i) => <li key={i}><strong>{item.package} {item.installed}</strong> · {item.id} · {item.severity}
          <p>{item.applied_versions?.length ? `Versions présentes dans le lockfile corrigé · ${item.applied_versions.join(', ')}. ` : ''}
          {item.fixed_versions.length ? `Versions correctives signalées par Trivy · ${item.fixed_versions.join(', ')}. Vérifier leur compatibilité avant de modifier le dépôt.` : 'Aucune version corrective signalée.'}</p></li>)}</ul>
      </details>}
    </div>}
    {report && <div><h3>Rapport SAST</h3>
      <p>{report.count} résultat(s) Semgrep · DeepSAST · {labels[report.ai?.status] || 'État inconnu'}</p>
      {report.ai?.status === 'COMPLETE' && <p><strong>{report.ai.analyzed}</strong> extrait(s) analysé(s) par le modèle local · {report.ai.scope === 'SOURCE' ? 'Échantillon du code source' : 'Emplacements signalés par Semgrep'}</p>}
      <p>DeepSAST reste consultatif. Semgrep conserve la décision sur le code.</p>
      {Object.entries(report.rules || {}).length > 0 && <details><summary>Règles Semgrep et pistes de correction</summary>
        <ul>{Object.entries(report.rules).map(([rule, count]) => <li key={rule}>{rule} · {count}<p>{run.remediation_suggestions?.[rule] || 'Vérifier le résultat avant toute correction.'}</p></li>)}</ul>
      </details>}
    </div>}
    {issue?.status === 'SENT' && <p>{repoUrl && issue.number ? <a href={`${repoUrl}/issues/${issue.number}`} target="_blank" rel="noopener noreferrer">Voir l’issue GitHub #{issue.number}</a> : 'Issue GitHub envoyée.'}</p>}
    {run.security_actions?.EMAIL?.status === 'SENT' && <p>Rapport envoyé par e-mail.</p>}
    {Object.values(run.security_actions || {}).some(action => action.status === 'UNCONFIRMED') && <p>Un envoi externe reste à confirmer. Vérifiez la destination.</p>}
    {terminal && warning && run.trigger_type !== 'ROLLBACK' && !run.security_feedback_enabled && <p>Envoi externe désactivé sur cette instance.</p>}
    {terminal && warning && run.trigger_type !== 'ROLLBACK' && run.security_feedback_enabled && <form onSubmit={submit}>
      <p>Envoyer un résumé sans extrait de code ni chemin privé.</p>
      <label className="field"><span>Destination</span><select value={channel} onChange={e => setChannel(e.target.value)}>
        <option value="ISSUE">Issue dans le dépôt</option>
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
