#!/usr/bin/env bash
# Idempotent local tunnel provisioning, invoked by the AUTOKOM provisioner.
set -euo pipefail
umask 077
[[ "$EUID" == 0 ]] || { echo "Exécuter avec sudo." >&2; exit 2; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
state=/etc/autokom-preview
if [[ -e "$state" || -L "$state" ]]; then
    [[ ! -L "$state" && "$(stat -c '%U:%a' "$state")" == root:700 ]] || exit 2
fi
install -d -m 0700 "$state"
exec 9>"$state/.setup.lock"
flock -n 9
# Idempotent and does not stop an existing preview tunnel.
for bin in wg wg-quick nft dnsmasq nginx flock; do
    command -v "$bin" >/dev/null || { echo "Commande manquante : $bin" >&2; exit 2; }
done
if ! nginx -V 2>&1 | grep -q -- '--with-http_realip_module'; then
    echo 'Module Nginx realip requis : aucune modification des services.' >&2; exit 2
fi
if ! systemctl is-active --quiet autokom-preview-vpn.service; then
    if ip link show dev akpreview >/dev/null 2>&1; then
        echo 'Interface akpreview déjà utilisée : aucune modification.' >&2; exit 2
    fi
    if [[ -n "$(ss -Huln 'sport = :51896')" || -n "$(ss -Htln 'sport = :18443')" && ! -f "$state/akpreview.conf" ]]; then
        echo 'Port dédié déjà utilisé : aucune modification.' >&2; exit 2
    fi
    if ip -4 route show table main | python3 -c 'import ipaddress,sys; net=ipaddress.ip_network("10.203.0.0/16"); sys.exit(not any(net.overlaps(ipaddress.ip_network(line.split()[0],strict=False)) for line in sys.stdin if line.strip() and line.split()[0][0].isdigit()))'; then
        echo 'Sous-réseau WireGuard déjà utilisé : aucune modification.' >&2; exit 2
    fi
fi
# DNS data is a separate, non-secret directory accessible to dnsmasq after dropping privileges.
hosts=/var/lib/autokom-preview-dns
[[ ! -L "$hosts" ]] || exit 2
install -d -o root -g root -m 0755 "$hosts"
for file in akpreview.conf server.pub peers.conf dnsmasq.conf firewall.nft; do
    [[ ! -L "$state/$file" ]] || exit 2
done
if [[ ! -f "$state/akpreview.conf" ]]; then
    [[ ! -e "$state/peers.json" ]] || exit 2
    private="$(wg genkey)"
    wg pubkey <<<"$private" >"$state/server.pub"
    printf '[Interface]\nPrivateKey = %s\nAddress = 10.203.53.1/16\nListenPort = 51896\n' "$private" >"$state/akpreview.conf"
    unset private
fi
[[ -f "$state/peers.conf" ]] || { [[ ! -e "$state/peers.json" ]] || exit 2; sed '/^Address = /d' "$state/akpreview.conf" >"$state/peers.conf"; }
[[ -s "$state/server.pub" ]] || exit 2
cat >"$state/dnsmasq.conf" <<'DNS'
listen-address=10.203.53.1
bind-interfaces
except-interface=lo
no-hosts
hostsdir=/var/lib/autokom-preview-dns
domain-needed
bogus-priv
local-ttl=5
DNS
install -m 0600 "$source_dir/firewall.nft" "$state/firewall.nft"
nft -c -f "$state/firewall.nft"
# Restore our table after a third-party firewall reload, without restarting peers.
nft -f "$state/firewall.nft"
for unit in vpn dns peers; do
    install -m 0644 "$source_dir/autokom-preview-$unit.service" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now autokom-preview-vpn.service autokom-preview-dns.service
echo 'Tunnel prêt. Vérifier les pare-feu et les accès depuis un client externe.'
