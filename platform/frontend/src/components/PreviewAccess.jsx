import { useEffect, useState } from 'react'
import { Download, KeyRound, LockKeyhole } from 'lucide-react'
import { projectsApi } from '../services/projectsApi.js'
import { errorText } from '../services/projectWorkflow.js'
import ActionDialog from './ActionDialog.jsx'

export default function PreviewAccess({ projectId, environment }) {
  const envId = environment.id
  const [info, setInfo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState(null)
  const isPrivate = info?.private ?? (environment.wireguard_only === true)

  useEffect(() => {
    let active = true
    projectsApi.previewAccess(projectId, envId).then(value => {
      if (active) { setInfo(value); setError('') }
    }).catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [projectId, envId, environment.wireguard_only, environment.publicationStatus])

  async function apply(action) {
    if (busy || !action) return
    setDialog(null)
    setBusy(true)
    setError('')
    try {
      if (action === 'issue') {
        // The attachment is streamed into a transient Blob. No private key
        // is written to UI state, logs, JSON exports or browser storage.
        const blob = await projectsApi.issuePreviewAccess(projectId, envId)
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = 'ak-' + envId.replaceAll('-', '').slice(0, 10) + '.conf'
        document.body.append(link)
        link.click()
        link.remove()
        window.setTimeout(() => URL.revokeObjectURL(url), 1000)
        setInfo(previous => ({ ...previous, issued: true }))
      } else if (action === 'revoke') {
        await projectsApi.revokePreviewAccess(projectId, envId)
        setInfo(previous => ({ ...previous, issued: false }))
      } else {
        await projectsApi.setPreviewAccess(projectId, envId, action === 'private')
        setInfo(await projectsApi.previewAccess(projectId, envId))
      }
    } catch (err) { setError(errorText(err)) }
    finally { setBusy(false) }
  }

  return <section className="ak-workspace-sheet" aria-label="Visibilité de la préproduction">
    <h2><LockKeyhole size={19}/> Accès à la préproduction</h2>
    <p>{environment.name} · <strong>{isPrivate ? 'WireGuard uniquement' : 'Public'}</strong></p>
    <p>Ce réglage ne modifie ni votre dépôt GitHub ni les autres environnements.</p>
    {error && <p className="feedback error" role="alert">{error}</p>}
    <div className="ak-project-detail-actions">
      <button type="button" className="button-outline" disabled={busy} onClick={() => isPrivate ? setDialog('public') : apply('private')}>
        {isPrivate ? 'Rendre la préproduction publique' : 'Masquer la préproduction avec WireGuard'}
      </button>
      {isPrivate && info?.ready && <button type="button" className="button-primary" disabled={busy}
        onClick={() => info.issued ? setDialog('issue') : apply('issue')}><Download size={16}/>
        {info.issued ? 'Remplacer le fichier WireGuard' : 'Télécharger le fichier WireGuard'}
      </button>}
      {isPrivate && info?.issued && <button type="button" className="button-outline" disabled={busy}
        onClick={() => setDialog('revoke')}><KeyRound size={16}/> Révoquer l’accès</button>}
    </div>
    {isPrivate && info?.ready && <>
      <p>Importez le fichier .conf dans WireGuard, activez le tunnel, puis ouvrez l’adresse ci-dessous. Transmettez le fichier au client par un canal privé.</p>
      {info.url && <p><a href={info.url} target="_blank" rel="noopener noreferrer">{info.url}</a></p>}
    </>}
    {isPrivate && info && !info.ready && <p>Le fichier sera disponible après le premier déploiement réussi.</p>}
    <ActionDialog open={Boolean(dialog)} title={dialog === 'public' ? 'Rendre ce site public ?' : dialog === 'issue' ? 'Remplacer le fichier WireGuard ?' : 'Révoquer l’accès WireGuard ?'}
      description={dialog === 'public' ? 'Le site sera accessible sur Internet sans VPN et le fichier actuel sera révoqué.' : dialog === 'issue' ? 'Le nouveau fichier remplacera le précédent, qui cessera de fonctionner.' : 'Le fichier actuel cessera de fonctionner et le site restera privé.'}
      variant="warning" confirmLabel="Confirmer" busy={busy} onClose={() => setDialog(null)} onConfirm={() => { void apply(dialog) }}/>
  </section>
}
