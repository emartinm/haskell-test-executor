#!/bin/sh
# Prepares the cgroups used by isolate inside the container (what isolate-cg-keeper
# does with systemd outside Docker), prepares the socket directory and runs the
# command as "executor".
#
# Requires --privileged (writable cgroupfs) and cgroups v2 with its own
# namespace, which is what Docker does by default on a cgroups v2 host.
#
# Socket: the directory /run/haskell-test-executor (mounted from the host) becomes
# owned by executor with mode 0750, keeping its group. Only the members of that
# group on the host (e.g. the Django user) can reach the socket.
set -eu

SOCKET_DIR=/run/haskell-test-executor

CG=/sys/fs/cgroup

if [ "$(stat -fc %T "$CG")" != cgroup2fs ]; then
    echo "entrypoint: cgroups v2 required" >&2
    exit 1
fi

# cgroups v2 rule: a group that contains processes cannot distribute
# controllers among its children. The container processes are moved to "init".
mkdir -p "$CG/init"
while read -r pid; do
    echo "$pid" > "$CG/init/cgroup.procs" 2>/dev/null || true
done < "$CG/cgroup.procs"

echo "+cpuset +memory" > "$CG/cgroup.subtree_control"
mkdir -p "$CG/isolate"
echo "+cpuset +memory" > "$CG/isolate/cgroup.subtree_control"

mkdir -p "$SOCKET_DIR"
chown "executor:$(stat -c %g "$SOCKET_DIR")" "$SOCKET_DIR"
chmod 0750 "$SOCKET_DIR"
rm -f "$SOCKET_DIR/executor.sock"

exec setpriv --reuid=executor --regid=executor --init-groups -- "$@"
