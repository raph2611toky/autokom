/** Real chronological view: technical includes every summary event plus diagnostics. */
import { cleanCustomerLog, phaseTitles, publicationLabel } from './deploymentPresentation.js'

const STAGE = /^AUTOKOM_STAGE:(source|sast|sca|build|release|health):(RUNNING|SUCCESS|FAILED|SKIPPED)$/
const TECH = /^AUTOKOM_TECH:(source|sast|sca|build|release|health):([\s\S]{1,800})$/

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
    if (item.level === 'TECH') {
      const raw = TECH.exec(item.message)
      return raw ? [{...item, stage_key:raw[1], display:raw[2]}] : []
    }
    const display = cleanCustomerLog(item.message)
    if (!display) return []
    return [{...item, stage_key:item.stage_key || current, display}]
  })
}
