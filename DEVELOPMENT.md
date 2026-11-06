# Guía de desarrollo

## Requisitos

- **Docker**, para construir la imagen y pasar las pruebas como en producción.
- Para las pruebas en local, fuera de Docker: Python 3, `ghc` (la misma versión que la imagen,
  `GHC_VERSION` en `docker/Dockerfile`) y, para las pruebas del sandbox,
  [isolate](https://github.com/ioi/isolate) instalado y pertenecer al grupo `isolate`. Sin
  `fastapi` se omiten las pruebas de la API.

## Pruebas

```sh
python3 -m unittest discover -t . -s runner/tests     # en local
bash harness/run_tests.sh                             # casos del harness
```

En la imagen, con los mismos permisos que en producción:

```sh
docker build -f docker/Dockerfile -t haskell-test-executor --build-arg REVISION=$(git rev-parse --short HEAD) .
docker run --rm --network none --cap-drop ALL --cap-add SYS_ADMIN --cap-add NET_ADMIN \
  --cap-add SETUID --cap-add SETGID --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SYS_CHROOT --security-opt apparmor=unconfined \
  haskell-test-executor python3 -m unittest discover -t . -s runner/tests
```

`REVISION` es el commit que devuelve `/health` (`unknown` si se omite).

Para probarlo con el juez en local, `config.sh` de
[haskell-judge](https://github.com/emartinm/haskell-judge) arranca un contenedor de esta imagen
con su socket en `/tmp/hj-executor/`.

## Cambiar de versión de GHC

En `docker/Dockerfile`, actualizar `GHC_VERSION` y `GHC_SHA256` (en el `SHA256SUMS` de
`https://downloads.haskell.org/~ghc/<versión>/`, la línea del fichero `GHC_BINDIST`),
reconstruir y pasar también `bash harness/run_tests.sh` en local con la misma versión.

## Cambiar de versión de Ubuntu

Actualizar `UBUNTU` y, en la línea del repositorio de isolate, `Suites: <nombre en clave>-isolate`.

## Rendimiento

`bench/` tiene un benchmark de GHC y una prueba de carga. En una máquina de desarrollo con 3
sandboxes, un envío típico tarda ~0,3 s (compilar ~0,12 s, enlazar con lld ~0,1 s, ejecutar
~0,02 s) y 30 envíos simultáneos se resuelven en ~3 s.
