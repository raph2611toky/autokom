// Managed by AUTOKOM: first-administrator initialization, ONCE on a pristine wizard.
// Never run on an existing completed Jenkins controller or change existing users.
import jenkins.model.Jenkins
import jenkins.install.InstallState
import jenkins.security.ApiTokenProperty
import hudson.model.User
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions

final def j = Jenkins.get()
final def base = new File(j.rootDir, '.autokom-firstboot')
final def secret = new File(base, 'admin.seed')
final def auth = new File(base, 'admin.auth.pending')

if (j.installState.isSetupComplete()) return
if (!secret.isFile() || auth.exists()) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: seed absent or token already pending')
}
if (!(j.securityRealm instanceof HudsonPrivateSecurityRealm) || j.securityRealm.allowsSignup()) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: nonstandard Jenkins security realm')
}
final def otherUsers = User.getAll().findAll { it.id != 'admin' }
if (!otherUsers.isEmpty()) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: existing Jenkins users; refusing to replace identities')
}
final def pair = secret.readLines('UTF-8')
if (pair.size() != 4 || !(pair[0] ==~ /[A-Za-z][A-Za-z0-9_.-]{2,39}/) ||
    pair[0] in ['admin', 'autokom-metrics'] || pair[1].length() < 16 ||
    pair[2].length() < 1 || pair[2].length() > 100 || !(pair[3] ==~ /[^\s@]+@[^\s@]+\.[^\s@]+/)) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: invalid bootstrap secret')
}
if (User.getById(pair[0], false) != null) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: administrator already exists')
}

final def realm = (HudsonPrivateSecurityRealm) j.securityRealm
final def person = realm.createAccount(pair[0], pair[1])
person.setFullName(pair[2])
person.save()
final def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
j.setAuthorizationStrategy(strategy)
final def property = person.getProperty(ApiTokenProperty.class)
if (property == null) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: API token support unavailable')
}
final def api = property.generateNewToken('AUTOKOM initial deployment automation')
if (!auth.createNewFile()) {
    throw new IllegalStateException('AUTOKOM_FIRSTBOOT: token file collision')
}
Files.setPosixFilePermissions(auth.toPath(), PosixFilePermissions.fromString('rw-------'))
auth.setText(pair[0] + ':' + api.plainValue + '\n', 'UTF-8')

// Email is stored in a root-private profile; applied after the Mailer plugin
// becomes available. Do not import optional plugin classes before installation.
// The temporary stock 'admin' must not retain the initial unlock password.
final def temporary = User.getById('admin', false)
if (temporary != null) temporary.delete()
secret.delete()
j.save()
j.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
println('AUTOKOM_FIRSTBOOT_READY')
