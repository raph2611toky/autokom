# Jenkins — Quick onboarding v2C

- Existing Jenkins: display verified URL `https://<domain>/me/security/`, ask Jenkins login and API token with hidden prompt, then install plugin profile.
- New Jenkins: guide official first-run wizard and wait for **completion marker**, not merely `config.xml` and `admin` user. After wizard, same token onboarding resumes automatically.
- Token only in memory and ephemeral private file `/run/autokom-jenkins-*/auth`; deleted after safe-restart or failure.
- One menu for recommended profile; custom path keeps individual choices.
- No password reset, no unauthenticated Groovy console, no disabling Jenkins security.
