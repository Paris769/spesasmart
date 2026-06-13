#!/usr/bin/env bash
# Egress firewall for the openclaw container network.
#
# Strategy: the docker network 172.30.10.0/24 is denied all outbound traffic
# by default. We then whitelist the resolved IPs of every host in
# egress-allowlist.txt. The list is refreshed every 5 minutes by systemd.
#
# Run with root, once, on the VPS:    sudo bash setup-egress-firewall.sh
#
# Why this approach and not a sidecar proxy?
# A sidecar (e.g. squid) can be bypassed if the LLM finds a way to read /etc/hosts
# or call raw IPs. iptables at the host level is hard to bypass from inside the
# container.

set -euo pipefail

NET_CIDR="172.30.10.0/24"
ALLOWLIST="${ALLOWLIST:-$(dirname "$0")/egress-allowlist.txt}"
CHAIN="OPENCLAW_EGRESS"

if [[ $EUID -ne 0 ]]; then
    echo "run as root" >&2; exit 1
fi

# 1. Create dedicated chain (idempotent)
if ! iptables -L "$CHAIN" >/dev/null 2>&1; then
    iptables -N "$CHAIN"
fi
iptables -F "$CHAIN"

# 2. Allow established / related (responses to allowed outbound)
iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# 3. Allow loopback within the docker bridge (intra-stack: openclaw → proxy)
iptables -A "$CHAIN" -d "$NET_CIDR" -j ACCEPT

# 4. Resolve each allowlisted hostname and ACCEPT outbound to those IPs
while IFS= read -r line; do
    host="${line%%#*}"; host="${host%% *}"; host="${host## *}"
    [[ -z "$host" ]] && continue
    # Resolve all A records; getent works for both IPv4 and IPv6
    while read -r _ ip; do
        # Only IPv4 for now (docker bridge is v4)
        [[ "$ip" =~ ^[0-9.]+$ ]] || continue
        iptables -A "$CHAIN" -d "$ip" -p tcp --dport 443 -j ACCEPT
        iptables -A "$CHAIN" -d "$ip" -p tcp --dport 80  -j ACCEPT
    done < <(getent ahostsv4 "$host" || true)
    echo "[allowed] $host"
done < "$ALLOWLIST"

# 5. Default-deny + log (sampled, to avoid filling syslog)
iptables -A "$CHAIN" -m limit --limit 10/min -j LOG --log-prefix "openclaw-egress-deny: " --log-level 4
iptables -A "$CHAIN" -j REJECT --reject-with icmp-port-unreachable

# 6. Hook the chain on the FORWARD path for our subnet
iptables -D FORWARD -s "$NET_CIDR" -j "$CHAIN" 2>/dev/null || true
iptables -I FORWARD -s "$NET_CIDR" -j "$CHAIN"

echo
echo "OK — openclaw egress firewall installed."
echo "Re-run this script (or wire it via cron / systemd timer every 5 min)"
echo "whenever you change egress-allowlist.txt, as IPs may change."
