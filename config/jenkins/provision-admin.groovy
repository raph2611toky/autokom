import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.Permission

// Executed ONLY through authenticated Jenkins CLI as an existing administrator.
// Never replaces existing security realm/authorization model or existing users.
def j = Jenkins.get()
if (!(j.securityRealm instanceof HudsonPrivateSecurityRealm)) {
    throw new IllegalStateException('AUTOKOM: administrateur automatisé limité au realm local Jenkins')
}
if (j.authorizationStrategy.class.name != 'hudson.security.GlobalMatrixAuthorizationStrategy') {
    throw new IllegalStateException('AUTOKOM: nécesssite Matrix Authorization globale active (ne modifie pas la stratégie)')
}
def creds = new File('__AUTOKOM_ADMIN_FILE__')
def lines = creds.readLines('UTF-8')
if (lines.size() != 2) throw new IllegalStateException('Secret admin invalide')
def login = lines[0]
def password = lines[1]
def existing = hudson.model.User.getById(login, false)
if (existing != null) {
    println('AUTOKOM_ADMIN_ALREADY_EXISTS')
} else {
    def realm = (HudsonPrivateSecurityRealm) j.securityRealm
    realm.createAccount(login, password)
    def strategy = j.authorizationStrategy
    strategy.add(Jenkins.ADMINISTER, login)
    j.save()
    println('AUTOKOM_ADMIN_CREATED')
}
