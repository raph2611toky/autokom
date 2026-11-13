export function githubRepositoryUrl(name) {
  if (typeof name !== 'string' || !/^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$/.test(name)) return null
  return 'https://github.com/' + name.split('/').map(encodeURIComponent).join('/')
}

export function securityWarning(run) {
  if (run?.sca_report?.remediated && run.status === 'SUCCEEDED') return 'Correctifs appliqués · à reporter dans GitHub'
  if (run?.security_warning || run?.sca_report?.count || run?.sca_report?.secrets || run?.sast_report?.count || run?.sast_report?.ai?.status === 'UNAVAILABLE') {
    return 'Résultats de sécurité à examiner'
  }
  return ''
}
