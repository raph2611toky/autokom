"""Trusted npm installation policy shared by the allowlisted AUTOKOM recipes.

A bad npm lock is *never* repaired in DevSecOps. In DevOps only, a one-time
repair is allowed inside the disposable Docker build context. The GitHub
checkout, committed lock and host filesystem are never modified.
"""

NPM_CI = 'npm ci --ignore-scripts --no-audit --no-fund'


def install_step(mode):
    if mode == 'DEVOPS':
        # Only recover from the specific npm EUSAGE lockfile mismatch, never
        # transient network, registry authentication, or another npm failure.
        return (
            'RUN set -eu; '
            'if ' + NPM_CI + ' >/tmp/autokom-npm-ci.log 2>&1; then '
            '  cat /tmp/autokom-npm-ci.log; '
            'elif grep -Fq "can only install packages when your package.json and package-lock.json" '
            '/tmp/autokom-npm-ci.log && '
            'grep -Fq "in sync" /tmp/autokom-npm-ci.log; then '
            '  echo "AUTOKOM_NPM_LOCK_REPAIRED: mode DEVOPS; dependances reconciliees uniquement pour ce build; corrigez package-lock.json dans GitHub."; '
            '  npm install --package-lock-only --ignore-scripts --no-audit --no-fund && '
            '  ' + NPM_CI + '; '
            'else cat /tmp/autokom-npm-ci.log; exit 1; fi\n'
        )
    return 'RUN ' + NPM_CI + '\n'
