#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Uso: ./rebuild-polkacrew.sh /ruta/a/PolkaCrew https://polkacrew-relay-xxxx.onrender.com"
  exit 1
fi

PROJECT="$(cd "$1" && pwd)"
RELAY_URL="${2%/}"

echo "==> Verificando relay"
curl -fsS "$RELAY_URL/health" >/dev/null
echo "✅ Relay OK: $RELAY_URL"

cd "$PROJECT"

echo "==> Compilando PolkaCrew con relay público"
VITE_POLKACREW_RELAY_URL="$RELAY_URL" npm run build

echo
echo "✅ dist/ generado con relay:"
echo "$RELAY_URL"
echo
echo "Siguiente paso:"
echo 'pad ./dist polkacrew.dot --env devnet --mnemonic "$MNEMONIC"'
