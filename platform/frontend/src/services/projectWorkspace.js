/** Espace frontend : conservation des configurations et aperçus dans ce navigateur.
 * Aucune donnée locale n'est présentée comme une publication réelle. */
import { inferPricingPlan } from '../config/pricing.js'
import { PREVIEW_KEY } from './simulationData.js'

export const DRAFTS_KEY = 'autokom.projects.drafts.v12'
export const PREVIEWS_KEY = 'autokom.ui.pipeline.previews.v14'

function readArray(key) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch { return [] }
}

export function getDrafts() { return readArray(DRAFTS_KEY) }
export function getPreviews() {
  const previews = readArray(PREVIEWS_KEY).filter(item => item && item.preview === true && item.id)
  // Ancien parcours V13 : conserver l'aperçu lors d'une mise à jour.
  try {
    const old = JSON.parse(localStorage.getItem(PREVIEW_KEY) || 'null')
    if (old?.preview === true && Array.isArray(old.stages)) {
      const previous = getDrafts().find(d => d.projectName === old.name && d.repositorySlug === old.repository)
      const legacyId = old.id || previous?.id || 'legacy-pipeline-preview'
      if (!previews.some(p => String(p.id) === String(legacyId))) previews.unshift({ ...old, id: legacyId })
    }
  } catch { /* Aucun ancien aperçu. */ }
  return previews
}

export function storePreview(preview) {
  if (!preview || !preview.id || !Array.isArray(preview.stages)) throw new Error('Aperçu incomplet')
  const next = [{ ...preview, preview: true }, ...getPreviews().filter(p => String(p.id) !== String(preview.id))].slice(0,40)
  localStorage.setItem(PREVIEWS_KEY, JSON.stringify(next))
  localStorage.setItem(PREVIEW_KEY, JSON.stringify(preview))
  return preview
}

export function getProjectRecords() {
  const drafts = getDrafts()
  const previews = getPreviews()
  const indexedPreviews = new Map(previews.map(p => [String(p.id),p]))
  const records = drafts.filter(Boolean).map(d => ({
    id: String(d.id), name: d.projectName || d.repositorySlug || 'Projet sans nom',
    repository: d.repositorySlug || '', branch: d.branch || '', workdir: d.workdir || './',
    environment: d.environment || 'production', mode: d.pipelineMode || 'devsecops',
    offer: d.resourcePlan || 'free', pricingPlan: inferPricingPlan(d), domainMode: d.domainMode || 'autokom', domain: d.domain || '',
    updatedAt: d.updatedAt || '', draft: d, preview: indexedPreviews.get(String(d.id)) || null,
  }))
  const known = new Set(records.map(r => r.id))
  for (const p of previews) {
    if (known.has(String(p.id))) continue
    records.push({
      id:String(p.id), name:p.name || 'Projet', repository:p.repository || '',
      branch:p.branch || '', workdir:p.workdir || './', environment:p.environment || 'production',
      mode:p.mode || 'devsecops', offer:p.offer || 'free', pricingPlan: inferPricingPlan(p), domainMode:p.domainMode || 'autokom',
      domain:p.domain || '', updatedAt:p.createdAt || '', draft:null, preview:p,
    })
  }
  return records.sort((a,b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
}

export function getProjectRecord(id) { return getProjectRecords().find(p => p.id === String(id)) || null }

export function removeProject(id) {
  const key = String(id)
  const previews = getPreviews()
  const matchingPreview = previews.find(preview => String(preview.id) === key)
  localStorage.setItem(DRAFTS_KEY, JSON.stringify(getDrafts().filter(d => String(d.id) !== key)))
  localStorage.setItem(PREVIEWS_KEY, JSON.stringify(previews.filter(p => String(p.id) !== key)))
  try {
    const old = JSON.parse(localStorage.getItem(PREVIEW_KEY) || 'null')
    if (String(old?.id) === key || (matchingPreview && old?.name === matchingPreview.name && old?.repository === matchingPreview.repository)) localStorage.removeItem(PREVIEW_KEY)
  } catch { /* Rien à nettoyer. */ }
}
