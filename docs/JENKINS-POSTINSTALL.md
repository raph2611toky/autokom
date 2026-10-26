# AUTOKOM — Jenkins First-Run / Post-Install v2C

This module **does not** circumvent the Jenkins official first-run wizard and **never** changes the existing authorization strategy or resets existing users.

## Interactive quick-start

```bash
sudo bash scripts/install/07-jenkins-postinstall.sh --check --interactive
sudo bash scripts/install/07-jenkins-postinstall.sh --apply --interactive
```

The first menu proposes **recommended** (one choice: DevSecOps plugins + non-destructive security settings) or **custom**. For a previously initialized Jenkins, the script skips the first-run wizard. For an uninitialized Jenkins it shows the detected, valid HTTPS Nginx domain and the path to `initialAdminPassword`, then waits until the Jenkins official setup wizard is complete (including first administrator creation and instance URL). To avoid duplicating plugins, choose **Select plugins to install → None → Install** during the wizard; AUTOKOM will install the selected profile afterward.

When Jenkins is initialized, AUTOKOM prints the documented `/me/security/` URL so the human administrator can create an API token. The token is **pasted in the terminal using hidden input**, stored in a temporary root-only file under `/run`, checked for `Overall/Administer`, and erased on exit. No manual `nano /etc/autokom/secrets/jenkins-cli-auth` step is necessary. The CLI uses `-auth @file`; the secret never appears in a process command line. Only one controlled safe-restart is requested after changes.

If no valid HTTPS Nginx Jenkins domain can be discovered:

```bash
sudo bash scripts/install/07-jenkins-postinstall.sh --apply --interactive \
  --public-url https://jenkins.example.org/
```

`--public-url` requires a URL using HTTPS at the root. Check that the domain really serves your Jenkins instance. If Jenkins does not yet have HTTPS, keep port 8080 on loopback and use an SSH tunnel for the initial wizard, then configure HTTPS **before pasting an API token**.

## Automation / CI

For a pre-configured Jenkins, an optional existing root-owned mode-0600 auth file containing `username:API_TOKEN` may be passed explicitly:

```bash
sudo bash scripts/install/07-jenkins-postinstall.sh --apply --profile devsecops \
  --auth-file /etc/autokom/secrets/jenkins-cli-auth --harden --confirm-restart
```

Never put token values on the command line or inside Git. The file option is for non-interactive automation only; the interactive mode does not require it.

## Scope and safeguards

- First-run wizard is only recognized when the official setup completion marker (`$JENKINS_HOME/jenkins.install.UpgradeWizard.state`) is missing, the initial password file is present, and no other accounts have been created. Completed Jenkins with broken security is **refused** instead of automatically reset.
- No automatic installation of insecure optional multibranch-scan-webhook-trigger unless explicitly selected through CLI option.
- Do not change existing Jenkins user passwords/authorization realm, LDAP, existing jobs, agents, or plugin versions silently.
- An *additional* administrator (not the first) can be created on supported Jenkins local realm + global Matrix strategy after admin authorization. Its password is entered with a hidden confirmation and used ephemerally.
- Use `/var/lib/jenkins/secrets/initialAdminPassword` only when Jenkins really requires its first-run wizard; do not print the secret in persistent logs.
- When `--check` is used, the wizard is never waited upon, and no token is requested.
- CLI link detection reads the active Nginx config via the previously added `nginx_discovery.py`; no new Nginx configuration or port exposure is performed by this patch.
- Before applying to a busy Jenkins controller, back up `$JENKINS_HOME` and review proposed plugin additions. `safe-restart` can wait for running jobs and may result in temporary unavailability. Confirm service and plugin health after restart.

## Validation

```bash
bash -n scripts/install/07-jenkins-postinstall.sh
python3 -m py_compile scripts/lib/jenkins_postinstall.py
python3 tests/test_jenkins_postinstall.py
python3 tests/test_jenkins_onboarding.py
```

Unit tests mock Jenkins/systemd; **real Jenkins / plugin / HTTPS validation is still necessary before production apply**.
