/** Real chronological view: technical includes every summary event plus diagnostics. */
import { cleanCustomerLog, phaseTitles, publicationLabel } from './deploymentPresentation.js'

const STAGE = /^AUTOKOM_STAGE:(source|sast|deepsast|sca|build|release|dast|health):(RUNNING|SUCCESS|FAILED|SKIPPED)$/
const TECH = /^AUTOKOM_TECH:(source|sast|deepsast|sca|build|release|dast|health):([\s\S]{1,800})$/
const DETAIL = /^AUTOKOM_TRACE:(source|sast|deepsast|sca|build|release|dast|health):(broker|docker|application|pipeline):([^\r\n]{1,700})$/

export function technicalTimeline(messages) {
  let current = 'source'
  return [...messages].sort((a,b) => a.sequence-b.sequence).flatMap(item => {
    if (!item || typeof item.message !== 'string') return []
    const stage = STAGE.exec(item.message)
    if (stage) {
      current = stage[1]
      const description = `${phaseTitles[current]} : ${publicationLabel(stage[2])[0]}`
      return [{...item, stage_key:current, display:description}]
    }
    if (item.level === 'DETAIL') {
      const trace = DETAIL.exec(item.message)
      return trace ? [{...item, stage_key:trace[1], channel:trace[2], display:trace[3], isDetail:true}] : []
    }
    if (item.level === 'TECH') {
      const raw = TECH.exec(item.message)
      return raw ? [{...item, stage_key:raw[1], display:raw[2]}] : []
    }
    const display = cleanCustomerLog(item.message)
    if (!display) return []
    return [{...item, stage_key:item.stage_key || current, display}]
  })
}

/** A real file-style line, constructed only from the server-filtered deployment log. */
export function formatTechnicalFileLine(entry) {
  const rawDate = new Date(entry?.created_at || 0)
  const time = Number.isNaN(rawDate.getTime()) ? '---- -- -- --:--:--' : rawDate.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, 'Z')
  const origin = entry?.channel || (entry?.isDetail ? 'application' : 'autokom')
  const severity = /(?:error|failed|échec|refus|operationalerror|traceback|permissionerror|notfounderror)/i.test(entry?.display || '')
    ? 'ERROR' : entry?.isDetail ? 'DEBUG' : entry?.level === 'TECH' ? 'INFO' : 'INFO'
  const stage = entry?.stage_key || 'source'
  const message = String(entry?.display || '').replace(/[\r\n\0-\x08\x0b\x0c\x0e-\x1f]/g, ' ').slice(0, 780)
  return {text:`${time} [${severity}] [${stage}/${origin}] ${message}`,severity}
}
