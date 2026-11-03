/**
 * Catalogue documentaire, distinct du registre de capacités de l'administrateur.
 * "Gratuit" désigne la licence ou l'offre : jamais l'infrastructure, les quotas ou le trafic.
 * Les coûts et conditions sont à confirmer auprès de chaque fournisseur.
 */
export const SERVICE_GROUPS = [
  {
    id: 'cicd', title: 'Intégration continue', summary: 'Construire, tester et préparer vos publications.',
    options: [
      { id: 'jenkins', name: 'Jenkins', tier: 'libre', tag: 'Logiciel libre', detail: 'Moteur CI/CD hébergé par AUTOKOM. L’hébergement et l’exploitation consomment des ressources.', capability: 'cicd.jenkins', url: 'https://www.jenkins.io/' },
      { id: 'github-actions', name: 'GitHub Actions', tier: 'quota', tag: 'Gratuit avec quotas', detail: 'Minutes incluses selon votre abonnement GitHub, puis facturation éventuelle. Nécessite un adaptateur.', capability: 'cicd.github-actions', url: 'https://github.com/pricing' },
    ],
  },
  {
    id: 'sast', title: 'Analyse de code', summary: 'Options d’analyse statique en complément des politiques de sécurité.',
    options: [
      { id: 'semgrep-ce', name: 'Semgrep Community', tier: 'libre', tag: 'Édition communautaire', detail: 'Analyse SAST avec moteur communautaire. Vérifier les conditions de licence selon l’usage SaaS.', capability: 'security.semgrep', url: 'https://semgrep.dev/products/community-edition/' },
      { id: 'semgrep-teams', name: 'Semgrep Teams', tier: 'payant', tag: 'Abonnement payant', detail: 'Fonctions gérées avancées ; compte et abonnement externe nécessaires.', capability: 'security.semgrep-teams', url: 'https://semgrep.dev/pricing/' },
      { id: 'sonarqube-cloud', name: 'SonarQube Cloud', tier: 'quota', tag: 'Gratuit / payant', detail: 'Offres Free, Team et Enterprise suivant vos besoins et limites.', capability: 'security.sonarqube-cloud', url: 'https://www.sonarsource.com/products/sonarqube/cloud/' },
    ],
  },
  {
    id: 'vuln', title: 'Vulnérabilités et dépendances', summary: 'Scanner les dépendances, dépôts et images.',
    options: [
      { id: 'trivy', name: 'Trivy', tier: 'libre', tag: 'Open source', detail: 'Scans de dépendances, conteneurs et configurations, si installé par l’admin.', capability: 'security.trivy', url: 'https://www.trivy.dev/' },
      { id: 'none', name: 'Aucun service supplémentaire', tier: 'inclus', tag: 'Sans supplément', detail: 'Les contrôles imposés par l’administrateur restent obligatoires.', capability: null },
    ],
  },
  {
    id: 'database', title: 'Base de données', summary: 'La base est facultative et ne se crée jamais sans intégration serveur.',
    options: [
      { id: 'none', name: 'Pas de base de données', tier: 'inclus', tag: 'Sans supplément', detail: 'Idéal pour une application statique ou un service sans persistance.', capability: null },
      { id: 'postgres-managed', name: 'PostgreSQL AUTOKOM', tier: 'quota', tag: 'Selon quotas AUTOKOM', detail: 'Service géré localement uniquement si l’administrateur le publie.', capability: 'database.postgresql' },
      { id: 'neon-free', name: 'Neon Free', tier: 'quota', tag: 'Offre gratuite limitée', detail: 'PostgreSQL externalisé, avec limites de calcul, stockage et trafic.', capability: 'database.neon', url: 'https://neon.com/pricing' },
      { id: 'neon-paid', name: 'Neon Launch / Scale', tier: 'payant', tag: 'Paiement à l’usage', detail: 'Pour besoins de production ; abonnement et facturation chez Neon.', capability: 'database.neon', url: 'https://neon.com/pricing' },
    ],
  },
  {
    id: 'observability', title: 'Surveillance', summary: 'Métriques et journaux, selon la collecte réellement disponible.',
    options: [
      { id: 'platform', name: 'Observabilité AUTOKOM', tier: 'inclus', tag: 'Selon infrastructure', detail: 'Prometheus, Grafana et logs s’ils sont exposés par AUTOKOM Core.', capability: 'observability.platform' },
      { id: 'grafana-free', name: 'Grafana Cloud Free', tier: 'quota', tag: 'Offre gratuite limitée', detail: 'Compte externe et limites de métriques, logs et rétention.', capability: 'observability.grafana-cloud', url: 'https://grafana.com/products/cloud/' },
      { id: 'grafana-pro', name: 'Grafana Cloud Pro', tier: 'payant', tag: 'Abonnement + usage', detail: 'Plan payant avec capacités et rétention accrues.', capability: 'observability.grafana-cloud', url: 'https://grafana.com/products/cloud/' },
    ],
  },
  {
    id: 'ai', title: 'Assistance intelligente', summary: 'L’IA reste facultative ; aucune suggestion n’est appliquée sans validation.',
    options: [
      { id: 'none', name: 'Configuration manuelle', tier: 'inclus', tag: 'Sans coût IA', detail: 'Tous les réglages restent disponibles sans fournisseur IA.', capability: null },
      { id: 'platform', name: 'IA fournie par AUTOKOM', tier: 'quota', tag: 'Selon quota admin', detail: 'Disponible uniquement si l’administrateur a configuré un fournisseur.', capability: 'ai.assistant' },
      { id: 'external', name: 'IA externe', tier: 'payant', tag: 'Coût selon fournisseur', detail: 'Utilisation soumise aux conditions et à la facturation de l’API choisie.', capability: 'ai.external' },
    ],
  },
]

export const DEFAULT_SERVICES = Object.freeze({
  cicd: 'jenkins', sast: 'semgrep-ce', vuln: 'trivy', database: 'none', observability: 'platform', ai: 'none',
})
export const SERVICE_TIER_LABELS = { libre: 'Logiciel libre', quota: 'Gratuit / limité', payant: 'Payant', inclus: 'Sans supplément' }

export function resolveCapability(capabilities, id) {
  if (!id) return 'not-required'
  const match = Array.isArray(capabilities) ? capabilities.find((item) => item.key === id || item.capability_key === id) : null
  if (!match) return 'unknown'
  if (match.enabled === false || match.status === 'disabled') return 'disabled'
  if (match.healthy === true || match.status === 'healthy' || match.status === 'available') return 'healthy'
  return 'unavailable'
}
