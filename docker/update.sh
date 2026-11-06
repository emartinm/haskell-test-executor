#!/bin/bash
# Updates the executor on the server: builds a new image from this repository (latest code and
# latest Ubuntu packages), runs the tests in it and only then replaces the image of the service.
# Run it as root from a clone of the repository:
#   sudo docker/update.sh              # git pull, build, tests, restart
#   sudo docker/update.sh --no-pull    # the same with the current checkout
# The service keeps running the previous image while the new one is built and tested. If the
# service does not work after the restart, the previous image is restored automatically.
# Images are tagged with the commit (haskell-test-executor:<commit>, plus :latest and :rollback);
# the KEEP (3) newest commit tags are kept. Other variables: IMAGE, SERVICE, SOCKET and
# BUILD_OPTS (options of docker build, "--pull --no-cache" by default).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-haskell-test-executor}
SERVICE=${SERVICE:-haskell-test-executor}
SOCKET=${SOCKET:-/run/haskell-test-executor/executor.sock}
KEEP=${KEEP:-3}
read -r -a BUILD_OPTS <<<"${BUILD_OPTS:---pull --no-cache}"
PULL=${1:-}
# Same options as docker/haskell-test-executor.service
CAPS=(--network none --cap-drop ALL --cap-add SYS_ADMIN --cap-add NET_ADMIN --cap-add SETUID
    --cap-add SETGID --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SYS_CHROOT
    --security-opt apparmor=unconfined)

step() { printf '\n==> [%s] %s\n' "$(date +%T)" "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "ejecútalo con sudo"
OWNER=$(stat -c %U "$REPO")
as_owner() { sudo -u "$OWNER" "$@"; }

# Only one update at a time (e.g. cron and a manual run)
exec 9>"/run/lock/$SERVICE-update.lock"
flock -n 9 || fail "ya hay otra actualización en marcha"

# The service answers /health and evaluates a small submission
works() {
    local result
    for _ in $(seq 1 30); do
        result=$(curl -sf --max-time 30 --unix-socket "$SOCKET" -H 'Content-Type: application/json' \
            -d '{"student_code": "f :: Int -> Int\nf x = x + 1\n", "tests": [{"expr": "f 1", "expected": "2"}]}' \
            http://executor/run 2>/dev/null) && break
        sleep 2
    done
    [[ "${result:-}" == *'"status":"OK"'* || "${result:-}" == *'"status": "OK"'* ]] &&
        curl -sf --unix-socket "$SOCKET" http://executor/health && echo
}

if [ "$PULL" != "--no-pull" ]; then
    step "Descargando cambios"
    as_owner git -C "$REPO" pull --ff-only
fi
COMMIT=$(as_owner git -C "$REPO" rev-parse --short HEAD)
echo "Versión: $(as_owner git -C "$REPO" log -1 --format='%h %s')"

step "Construyendo la imagen ($IMAGE:candidate)"
docker build "${BUILD_OPTS[@]}" --build-arg "REVISION=$COMMIT" --label "org.opencontainers.image.revision=$COMMIT" \
    -f "$REPO/docker/Dockerfile" -t "$IMAGE:candidate" "$REPO"

step "Tests en la imagen nueva"
if ! docker run --rm "${CAPS[@]}" "$IMAGE:candidate" python3 -m unittest discover -t . -s runner/tests; then
    docker rmi "$IMAGE:candidate" >/dev/null
    fail "los tests fallan con la imagen nueva: el servicio sigue con la anterior"
fi

step "Cambiando de imagen"
OLD_ROLLBACK=$(docker image inspect -f '{{.Id}}' "$IMAGE:rollback" 2>/dev/null || true)
if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
    docker tag "$IMAGE:latest" "$IMAGE:rollback"
fi
docker tag "$IMAGE:candidate" "$IMAGE:latest"
docker tag "$IMAGE:candidate" "$IMAGE:$COMMIT"
docker rmi "$IMAGE:candidate" >/dev/null
# The previous rollback image is removed if nothing else tags it
if [ -n "$OLD_ROLLBACK" ] && [ -z "$(docker image inspect -f '{{join .RepoTags ""}}' "$OLD_ROLLBACK" 2>/dev/null)" ]; then
    docker rmi "$OLD_ROLLBACK" >/dev/null
fi
echo "$IMAGE:latest = $IMAGE:$COMMIT"

UNIT=/etc/systemd/system/$SERVICE.service
if ! cmp -s "$REPO/docker/haskell-test-executor.service" "$UNIT"; then
    echo "AVISO: $UNIT es distinto de docker/haskell-test-executor.service: revísalo y, si hay" \
        "que cambiarlo, cópialo y ejecuta systemctl daemon-reload y restart."
fi

step "Reiniciando $SERVICE"
systemctl restart "$SERVICE"
if ! works; then
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    if ! docker image inspect "$IMAGE:rollback" >/dev/null 2>&1; then
        fail "el servicio no funciona con la imagen nueva y no hay imagen anterior a la que volver"
    fi
    step "El servicio no funciona: volviendo a la imagen anterior"
    docker tag "$IMAGE:rollback" "$IMAGE:latest"
    systemctl restart "$SERVICE"
    works || fail "tampoco funciona con la imagen anterior: revisa el servicio"
    fail "el servicio no funcionaba con la imagen nueva; se ha restaurado la anterior"
fi

step "Borrando imágenes antiguas (se conservan las $KEEP más recientes)"
docker image ls "$IMAGE" --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | cut -f2 |
    grep -vxE 'latest|rollback|candidate|<none>' | tail -n "+$((KEEP + 1))" |
    while read -r tag; do docker rmi "$IMAGE:$tag" || true; done
docker image ls "$IMAGE"

echo
echo "OK: $SERVICE funciona con $IMAGE:$COMMIT"
