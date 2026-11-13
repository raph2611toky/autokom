#!/usr/bin/env bash
# Deploy only the fixed, root-owned staging checkout on the AUTOKOM primary VM.
set -Eeuo pipefail
root=/opt/autokom-preprod
src="$root/source"
etc=/etc/autokom-preprod
state=/var/lib/autokom-preprod
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
action="${1:-check}"
[[ "$EUID" == 0 && "$repo" == "$src" ]] || { echo "Exécuter avec sudo depuis $src." >&2; exit 2; }
[[ "$(git -C "$src" branch --show-current)" == master ]] || { echo 'Checkout master obligatoire.' >&2; exit 2; }
[[ "$(stat -c '%U:%a' "$src")" == root:755 ]] || { echo 'Checkout attendu root:0755.' >&2; exit 2; }
[[ -z "$(git -C "$src" status --porcelain --untracked-files=all)" ]] || { echo 'Checkout modifié, arrêter avant déploiement.' >&2; exit 2; }

for bin in python3 mysql nginx certbot git npm node curl dig systemctl flock; do
    command -v "$bin" >/dev/null || { echo "Commande absente : $bin" >&2; exit 2; }
done
getent passwd autokom-builder >/dev/null
systemctl is-active --quiet mysql.service
systemctl is-active --quiet nginx.service
test -d /var/lib/autokom/client-runtime
nginx -t
[[ "$action" != check ]] || { echo 'Précontrôles hôte réussis. Aucun service modifié.'; exit 0; }
install -d -o root -g root -m 0700 /run/lock/autokom-preprod
exec 9>/run/lock/autokom-preprod/deploy.lock
flock -n 9 || { echo 'Déploiement préproduction déjà en cours.' >&2; exit 2; }

if [[ "$action" == enable-worker ]]; then
    curl -fsS --noproxy '*' --max-time 15 https://pre.auto.ikom.mg/api/health/ >/dev/null
    python3 "$repo/scripts/preprod/configure.py" validate-worker
    systemctl enable --now autokom-preprod-worker.service
    exit
fi

if [[ "$action" == model ]]; then
    PYTHONPATH="$repo/scripts/lib" python3 -c 'from autokom_deepsast_admin import install; install()'
    exit
fi

[[ "$action" == init || "$action" == deploy ]] || { echo 'Actions : check, init, deploy, model, enable-worker.' >&2; exit 2; }
id autokom-preprod >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin autokom-preprod
install -d -o root -g root -m 0700 "$etc"
install -d -o root -g root -m 0755 "$state"
install -d -o root -g root -m 0700 "$state/client-runtime"
install -d -o autokom-preprod -g autokom-preprod -m 0750 "$state/media"
install -d -o autokom-preprod -g autokom-preprod -m 0755 "$state/static"
install -d -o root -g autokom-builder -m 2770 "$state/build-artifacts"
python3 "$repo/scripts/preprod/configure.py" init
if [[ "$action" == init ]]; then
    echo 'Préproduction initialisée. Compléter les fichiers privés avant deploy.'
    exit
fi

# Application and builds remain readable, but are not writable by either service account.
if systemctl is-active --quiet autokom-preprod-worker.service; then
    if ! python3 "$repo/scripts/preprod/configure.py" busy; then
        echo 'Impossible de confirmer une file préproduction inactive. Réessayer après les exécutions.' >&2
        exit 2
    fi
    systemctl stop autokom-preprod-worker.service
fi
python3 -m venv "$root/venv"
"$root/venv/bin/pip" install --quiet -r "$src/platform/backend/requirements.txt"
work="$(mktemp -d "$state/.frontend-XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
install -d -m 0700 -o autokom-builder -g autokom-builder "$work/build" "$work/cache"
chmod 0711 "$work"
git -C "$src" archive HEAD platform/frontend | tar -xf - -C "$work/build" --strip-components=2
chown -R autokom-builder:autokom-builder "$work/build"
runuser -u autokom-builder -- env -i PATH="$PATH" HOME="$work/cache" npm --prefix "$work/build" ci --ignore-scripts --no-audit --no-fund
runuser -u autokom-builder -- env -i PATH="$PATH" HOME="$work/cache" npm --prefix "$work/build" test
runuser -u autokom-builder -- env -i PATH="$PATH" HOME="$work/cache" VITE_AUTOKOM_API_BASE=/api VITE_AUTOKOM_AUTH_MODE=cookie VITE_AUTOKOM_FRONTEND_ONLY=false VITE_AUTOKOM_API_ENABLED=true VITE_AUTOKOM_GITHUB_ENABLED=true VITE_AUTOKOM_PLATFORM_PREPROD=true npm --prefix "$work/build" run build

python3 "$repo/scripts/preprod/configure.py" django check --deploy
python3 "$repo/scripts/preprod/configure.py" django migrate --noinput
python3 "$repo/scripts/preprod/configure.py" django seed_project_offers
python3 "$repo/scripts/preprod/configure.py" django collectstatic --noinput
python3 "$repo/scripts/preprod/configure.py" assets "$work/build/dist"
for name in web broker worker; do
    unit="autokom-preprod-$name.service"
    install -o root -g root -m 0644 "$repo/scripts/preprod/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable autokom-preprod-web.service autokom-preprod-broker.service
systemctl restart autokom-preprod-web.service autokom-preprod-broker.service
for i in {1..15}; do
    if curl -fsS --max-time 3 -H 'Host: pre.auto.ikom.mg' -H 'X-Forwarded-Proto: https'         http://127.0.0.1:8272/api/health/ >/dev/null; then break; fi
    [[ "$i" -lt 15 ]] || { echo 'Gunicorn ou la base de préproduction ne répond pas.' >&2; exit 2; }
    sleep 1
done
python3 "$repo/scripts/preprod/configure.py" publish
curl -fsS --noproxy '*' --max-time 15 --resolve 'pre.auto.ikom.mg:443:127.0.0.1' https://pre.auto.ikom.mg/api/health/
printf '\n'
echo 'Console préproduction publiée. Activer le worker seulement après préparation du job Jenkins dédié.'
