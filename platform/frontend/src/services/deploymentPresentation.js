/** Customer-safe status labels: no internal tools, ports or infrastructure IDs. */
export const publicationStatus = Object.freeze({
  QUEUED: ['En attente', 'waiting'],
  RUNNING: ['En cours', 'running'],
  SUCCEEDED: ['En ligne', 'success'],
  SUCCESS: ['Terminé', 'success'],
  FAILED: ['Échec', 'danger'],
  BLOCKED: ['Action requise', 'danger'],
  CANCELLED: ['Annulé', 'muted'],
  SKIPPED: ['Non requis', 'muted'],
  PENDING: ['En attente', 'waiting'],
})
export const phaseTitles = Object.freeze({
  source: 'Récupération du projet',
  sast: 'Analyse du code',
  sca: 'Contrôles de sécurité',
  build: 'Préparation de l’application',
  release: 'Publication',
  health: 'Vérification du site',
})
export const phaseOrder = ['source','sast','sca','build','release','health']
export function publicationLabel(status) {
  return publicationStatus[String(status || '').toUpperCase()] || ['À vérifier', 'muted']
}
export function cleanCustomerLog(message) {
  const value = String(message || '').replace(/^AUTOKOM_LOG:\s*/, '').trim()
  if (!value || value.startsWith('AUTOKOM_STAGE:')) return ''
  if (/Jenkins|BuildKit|runner|Docker|token|socket|credential|HMAC|API secret/i.test(value)) {
    return 'Étape de préparation en cours de vérification.'
  }
  return value.slice(0, 240)
}
