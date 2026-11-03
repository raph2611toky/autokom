/** Préférences d'interface. Les quotas définitifs seront décidés par IKOM. */
export const FREE_PRESET = Object.freeze({
  cpuLimit: '0.25', ramLimitMb: '256', storageGb: '1', serverCount: 1,
})

export function validateProjectSelection(form) {
  if (!form.githubRepositoryId || !/^\d+$/.test(String(form.githubRepositoryId))) return 'Choisissez un dépôt GitHub.'
  if (!form.repositorySlug || !/^[\w.-]+\/[\w.-]+$/.test(form.repositorySlug)) return 'Sélectionnez un dépôt accessible.'
  if (!form.branch?.trim()) return 'Sélectionnez la branche à déployer.'
  if (!form.projectName?.trim()) return 'Indiquez un nom de projet.'
  if (!form.workdir) return 'Sélectionnez le dossier source.'
  return ''
}
