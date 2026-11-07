// Managed by AUTOKOM: replace the TEMPORARY firstboot authorization only.
// Requires matrix-auth to be active; existing secured controllers are never touched.
import jenkins.model.Jenkins
import hudson.model.User
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.GlobalMatrixAuthorizationStrategy
import jenkins.security.ApiTokenProperty
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions

final def j = Jenkins.get()
final def home = new File(j.rootDir, '.autokom-firstboot')
final def pending = new File(home, 'matrix.pending')
final def done = new File(home, 'matrix.done')
final def metricsSeed = new File(home, 'metrics.seed')
final def metricsAuth = new File(home, 'metrics.auth.pending')
if (!pending.isFile() || done.exists()) return
if (!j.installState.isSetupComplete()) throw new IllegalStateException('AUTOKOM_MATRIX: setup not complete')
if (!(j.securityRealm instanceof HudsonPrivateSecurityRealm) || j.securityRealm.allowsSignup()) {
    throw new IllegalStateException('AUTOKOM_MATRIX: unexpected security realm')
}
if (!(j.authorizationStrategy instanceof FullControlOnceLoggedInAuthorizationStrategy)) {
    throw new IllegalStateException('AUTOKOM_MATRIX: authorization strategy already changed; preserved')
}
final def ids = User.getAll().collect { it.id }
if (ids.size() != 1 || ids[0] != 'autokom-admin') {
    throw new IllegalStateException('AUTOKOM_MATRIX: unexpected users; refusing authorization changes')
}
if (j.pluginManager.getPlugin('matrix-auth')?.active != true) {
    throw new IllegalStateException('AUTOKOM_MATRIX: matrix-auth not active')
}
final def strategy = new GlobalMatrixAuthorizationStrategy()
strategy.add(Jenkins.ADMINISTER, 'autokom-admin')
if (metricsSeed.isFile()) {
    if (j.pluginManager.getPlugin('metrics')?.active != true || metricsAuth.exists()) {
        throw new IllegalStateException('AUTOKOM_MATRIX: Metrics plugin absent or pending token collision')
    }
    final def data = metricsSeed.readLines('UTF-8')
    if (data.size() != 2 || data[0] != 'autokom-metrics' || data[1].length() < 24 ||
        User.getById('autokom-metrics', false) != null) {
        throw new IllegalStateException('AUTOKOM_MATRIX: metrics account conflict')
    }
    final def metricsUser = ((HudsonPrivateSecurityRealm) j.securityRealm).createAccount(data[0], data[1])
    final def apiProperty = metricsUser.getProperty(ApiTokenProperty.class)
    if (apiProperty == null) throw new IllegalStateException('AUTOKOM_MATRIX: Metrics token API unavailable')
    final def metricsToken = apiProperty.generateNewToken('AUTOKOM Prometheus read-only scrape')
    if (!metricsAuth.createNewFile()) throw new IllegalStateException('AUTOKOM_MATRIX: Metrics token file collision')
    Files.setPosixFilePermissions(metricsAuth.toPath(), PosixFilePermissions.fromString('rw-------'))
    metricsAuth.setText('autokom-metrics:' + metricsToken.plainValue + '\n', 'UTF-8')
    strategy.add(Jenkins.READ, 'autokom-metrics')
    strategy.add(jenkins.metrics.api.Metrics.VIEW, 'autokom-metrics')
    metricsSeed.delete()
}
j.setAuthorizationStrategy(strategy)
j.save()
if (!done.createNewFile()) throw new IllegalStateException('AUTOKOM_MATRIX: marker collision')
Files.setPosixFilePermissions(done.toPath(), PosixFilePermissions.fromString('rw-------'))
println('AUTOKOM_MATRIX_RESTRICTED')
