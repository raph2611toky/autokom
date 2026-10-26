import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.csrf.DefaultCrumbIssuer

// AUTOKOM deliberate no-op on authz/realm: never lock out existing admins.
def j = Jenkins.get()
if (j.authorizationStrategy.class.name.contains('Unsecured') ||
    j.securityRealm.class.name.contains('None')) {
    throw new IllegalStateException('AUTOKOM: Jenkins non sécurisé, intervention administrateur requise')
}
if (j.securityRealm instanceof HudsonPrivateSecurityRealm) {
    if (j.securityRealm.allowsSignup()) {
        throw new IllegalStateException('AUTOKOM: inscription publique active; désactiver avec la configuration de sécurité Jenkins avant automatisation')
    }
}
if (j.crumbIssuer == null) {
    j.setCrumbIssuer(new DefaultCrumbIssuer(true))
}
if (__AUTOKOM_DISABLE_AGENTS__) {
    j.setSlaveAgentPort(-1) // explicit opt-in: inbound agents may be in use
}
if (__AUTOKOM_CONTROLLER_ZERO__) {
    j.setNumExecutors(0)
}
j.save()
println('AUTOKOM_HARDENED')
