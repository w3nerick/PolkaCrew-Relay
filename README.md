# PolkaCrew Relay · Render deploy pack

Este paquete despliega el relay realtime de PolkaCrew como un Web Service HTTPS.
No necesitas dominio propio. Render asigna una URL tipo:

    https://polkacrew-relay-xxxx.onrender.com

## Opción recomendada: subir esta carpeta a GitHub

1. Crea un repo nuevo, por ejemplo `PolkaCrew-Relay`.
2. Sube todos los archivos de esta carpeta a la raíz del repo.
3. En Render: New → Blueprint.
4. Conecta ese repo.
5. Render detectará `render.yaml` y creará `polkacrew-relay`.
6. Cuando termine, copia la URL pública HTTPS que te entregue Render.

El Blueprint ya configura:

- Docker
- `/health`
- origen permitido: `https://polkacrew.dev-dot.li`
- rate limit del relay
- plan gratuito para pruebas

## Verificar el relay

    ./verify-relay.sh https://TU-URL.onrender.com

Debe devolver JSON con `"ok": true`.

## Recompilar PolkaCrew

    ./rebuild-polkacrew.sh /ruta/a/PolkaCrew https://TU-URL.onrender.com

Ese comando prueba `/health` y ejecuta:

    VITE_POLKACREW_RELAY_URL=https://TU-URL.onrender.com npm run build

Después vuelve a publicar el nuevo `dist/`:

    pad ./dist polkacrew.dot --env devnet --mnemonic "$MNEMONIC"

Nunca pegues tu mnemonic en un archivo del repo. Usa una variable de shell.

## Prueba multiplayer

Tras republicar el `dist/`:

1. Abre `https://polkacrew.dev-dot.li` en navegador A.
2. Crea una sala.
3. Abre la misma URL en navegador B.
4. Entra con el código.
5. Ambos deben mostrar NETWORK CONNECTED y verse mutuamente.

## Nota sobre el plan gratuito

El plan gratuito sirve para pruebas, pero el servicio puede dormirse por inactividad.
Para un juego público con conexiones realtime/SSE frecuentes, usa más adelante una
instancia que permanezca activa.
