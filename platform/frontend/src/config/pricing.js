/**
 * Tarification de travail pour la conception de l'offre AUTOKOM/IKOM.
 * Les montants et limites sont indicatifs tant que la grille commerciale n'est pas validée.
 * Un plan ne déclenche aucun paiement, et ses ressources ne sont pas réservées côté frontend.
 */
export const PRICING_PLANS = Object.freeze([
  {
    id: 'free',
    name: 'Découverte',
    tagline: 'Pour commencer sans frais.',
    monthly: 0,
    annual: 0,
    highlighted: false,
    capacity: '0,25 vCPU · 256 Mio RAM · 1 Gio',
    servers: '1 serveur',
    projects: '1 projet',
    cta: 'Commencer gratuitement',
    benefits: ['GitHub et branches', 'Déploiement d’un projet', 'Sous-domaine AUTOKOM', 'Pipelines DevOps / DevSecOps', 'Journaux essentiels'],
  },
  {
    id: 'essential',
    name: 'Essentiel',
    tagline: 'Pour publier une vraie application.',
    monthly: 49000,
    annual: 490000,
    highlighted: true,
    capacity: 'Profil recommandé après analyse',
    servers: '1 serveur',
    projects: 'Projets selon quotas',
    cta: 'Choisir Essentiel',
    benefits: ['Tout dans Découverte', 'Ressources adaptées au projet', 'Domaine personnalisé', 'Suivi des déploiements', 'Stockage et quotas évolutifs'],
  },
  {
    id: 'pro',
    name: 'Professionnel',
    tagline: 'Pour les services qui doivent durer.',
    monthly: 149000,
    annual: 1490000,
    highlighted: false,
    capacity: 'Profil recommandé après analyse',
    servers: 'Jusqu’à 2 serveurs',
    projects: 'Projets selon quotas',
    cta: 'Choisir Professionnel',
    benefits: ['Tout dans Essentiel', 'Option haute disponibilité', 'Deux serveurs distincts au maximum', 'Mise à l’échelle encadrée', 'Suivi renforcé des opérations'],
  },
])

export const PRICING_STORAGE_KEY = 'autokom.ui.preferred-plan'
export const LEGACY_PRICING_STORAGE_KEY = 'autokom.ui.preferred-offer'
export const DEFAULT_PRICING_PLAN = 'free'
export const BILLING_STORAGE_KEY = 'autokom.ui.billing-period'

export function findPricingPlan(id) {
  return PRICING_PLANS.find(plan => plan.id === id) || PRICING_PLANS[0]
}

export function inferPricingPlan(record = {}) {
  const declared = record.pricingPlan || record.pricing_plan
  if (PRICING_PLANS.some(plan => plan.id === declared)) return declared
  return record.resourcePlan === 'adapted' || record.offer === 'adapted'
    ? (record.highAvailability || record.high_availability_requested ? 'pro' : 'essential')
    : 'free'
}

export function getBillingPeriod(storage = (typeof localStorage === 'undefined' ? null : localStorage)) {
  try { return storage?.getItem(BILLING_STORAGE_KEY) === 'annual' ? 'annual' : 'monthly' }
  catch { return 'monthly' }
}

export function planToDeployment(id) {
  const plan = findPricingPlan(id)
  return {
    pricingPlan: plan.id,
    resourcePlan: plan.id === 'free' ? 'free' : 'adapted',
    // A paid offer is not an HA consent or a server-side entitlement.
    // High availability must always be explicitly selected by the customer.
    highAvailability: false,
  }
}

export function getPreferredPlan(storage = (typeof localStorage === 'undefined' ? null : localStorage)) {
  try {
    const saved = storage?.getItem(PRICING_STORAGE_KEY)
    if (saved && PRICING_PLANS.some(p => p.id === saved)) return saved
    return storage?.getItem(LEGACY_PRICING_STORAGE_KEY) === 'adapted' ? 'essential' : DEFAULT_PRICING_PLAN
  } catch { return DEFAULT_PRICING_PLAN }
}

export function rememberPlan(id, storage = (typeof localStorage === 'undefined' ? null : localStorage), period = 'monthly') {
  const plan = findPricingPlan(id)
  try {
    storage?.setItem(PRICING_STORAGE_KEY, plan.id)
    storage?.setItem(BILLING_STORAGE_KEY, period === 'annual' ? 'annual' : 'monthly')
    storage?.setItem(LEGACY_PRICING_STORAGE_KEY, plan.id === 'free' ? 'free' : 'adapted')
  } catch { /* Mode navigation privée : le parcours reste accessible. */ }
  return plan
}

export function formatAr(value) {
  return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(value)} Ar`
}
