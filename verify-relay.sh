#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Uso: ./verify-relay.sh https://polkacrew-relay-xxxx.onrender.com"
  exit 1
fi

URL="${1%/}"

echo "==> Health check"
curl -fsS "$URL/health"
echo
echo
echo "✅ Relay responde."
echo "Usa esta URL como VITE_POLKACREW_RELAY_URL:"
echo "$URL"
