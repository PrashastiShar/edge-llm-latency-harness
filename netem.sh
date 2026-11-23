#!/usr/bin/env bash
# Packet-level network emulation with Linux tc netem. Run on a real Linux host
# (e.g. the cloud VM), not in WSL. Needs root.
#   sudo ./netem.sh on eth0 80ms 40ms 1%     # delay, jitter, loss
#   sudo ./netem.sh off eth0
set -euo pipefail
case "${1:-}" in
  on)  tc qdisc replace dev "$2" root netem delay "$3" "$4" distribution normal loss "${5:-0%}"
       tc qdisc show dev "$2" ;;
  off) tc qdisc del dev "$2" root 2>/dev/null || true; tc qdisc show dev "$2" ;;
  *)   echo "usage: $0 on IFACE DELAY JITTER [LOSS] | off IFACE"; exit 1 ;;
esac
