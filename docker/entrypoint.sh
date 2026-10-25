#!/bin/sh
# Prepara los cgroups que usa isolate dentro del contenedor (lo que fuera de
# Docker hace isolate-cg-keeper con systemd), prepara el directorio del socket
# y ejecuta el comando como "judge".
#
# Requiere --privileged (cgroupfs escribible) y cgroups v2 con namespace propio,
# que es lo que Docker hace por defecto en un host con cgroups v2.
#
# Socket: el directorio /run/haskell-judge (montado desde el host) pasa a ser
# de judge con permisos 0750, conservando su grupo. Solo los miembros de ese
# grupo en el host (p. ej. el usuario de Django) pueden llegar al socket.
set -eu

SOCKET_DIR=/run/haskell-judge

CG=/sys/fs/cgroup

if [ "$(stat -fc %T "$CG")" != cgroup2fs ]; then
    echo "entrypoint: se necesitan cgroups v2" >&2
    exit 1
fi

# Regla de cgroups v2: un grupo que contiene procesos no puede repartir
# controladores entre sus hijos. Se mueven los procesos del contenedor a "init".
mkdir -p "$CG/init"
while read -r pid; do
    echo "$pid" > "$CG/init/cgroup.procs" 2>/dev/null || true
done < "$CG/cgroup.procs"

echo "+cpuset +memory" > "$CG/cgroup.subtree_control"
mkdir -p "$CG/isolate"
echo "+cpuset +memory" > "$CG/isolate/cgroup.subtree_control"

mkdir -p "$SOCKET_DIR"
chown "judge:$(stat -c %g "$SOCKET_DIR")" "$SOCKET_DIR"
chmod 0750 "$SOCKET_DIR"
rm -f "$SOCKET_DIR/judge.sock"

exec setpriv --reuid=judge --regid=judge --init-groups -- "$@"
