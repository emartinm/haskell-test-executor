# Secure Haskell test executor: GHC + isolate + runner.
#
#   docker build -f docker/Dockerfile -t haskell-test-executor .
#
#   # service: listens on /run/haskell-test-executor/executor.sock, without network
#   docker run --rm --privileged --network none -v /run/haskell-test-executor:/run/haskell-test-executor haskell-test-executor
#
#   # tests
#   docker run --rm --privileged haskell-test-executor python3 -m unittest discover -t . -s runner/tests
#
# --privileged is required so that isolate can create namespaces and manage the
# cgroups of its boxes. The host needs cgroups v2 and kernel >= 5.7.

ARG UBUNTU=26.04

# --- GHC in /opt/ghc ----------------------------------------------------------
FROM ubuntu:${UBUNTU} AS ghc

ARG GHC_VERSION=9.10.3
ARG GHC_BINDIST=ghc-${GHC_VERSION}-x86_64-deb12-linux
ARG GHC_SHA256=1ac63f04eac0ad551d45cbde38f27e0e3f43ceefd98833fae1fa3f2dbd042367

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils make gcc g++ libc6-dev libgmp-dev libffi-dev libnuma-dev \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/ghc.tar.xz https://downloads.haskell.org/~ghc/${GHC_VERSION}/${GHC_BINDIST}.tar.xz \
 && echo "${GHC_SHA256}  /tmp/ghc.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/ghc.tar.xz -C /tmp \
 && cd /tmp/ghc-${GHC_VERSION}-* \
 && ./configure --prefix=/opt/ghc \
 && make install \
 && rm -rf /tmp/ghc*

# The executor uses neither profiling nor documentation.
RUN find /opt/ghc \( -name '*.p_hi' -o -name '*.p_o' -o -name '*_p.a' -o -name '*.p_dyn_hi' -o -name '*.p_dyn_o' \) -delete \
 && rm -rf /opt/ghc/share/doc /opt/ghc/lib/*/html

# --- Final image ----------------------------------------------------------------
FROM ubuntu:${UBUNTU}

# GHC uses gcc and binutils to assemble; lld links much faster than ld.bfd.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gcc libc6-dev libgmp-dev libffi-dev libnuma-dev lld \
      python3 python3-fastapi python3-uvicorn python3-httpx \
 && install -d /etc/apt/keyrings \
 && curl -fsSL -o /etc/apt/keyrings/isolate.asc https://www.ucw.cz/isolate/debian/signing-key.asc \
 && printf 'Types: deb\nURIs: http://www.ucw.cz/isolate/debian/\nSuites: resolute-isolate\nComponents: main\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/isolate.asc\n' \
      > /etc/apt/sources.list.d/isolate.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends isolate \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghc /opt/ghc /opt/ghc

# Fixed uid/gid: the socket directory on the host ends up owned by this uid, so
# it must not match any user on the host.
ARG EXECUTOR_UID=6000

# Without systemd there is no isolate-cg-keeper: the entrypoint prepares this cgroup.
RUN sed -i 's|^cg_root = .*|cg_root = /sys/fs/cgroup/isolate|' /etc/isolate \
 && groupadd --gid ${EXECUTOR_UID} executor \
 && useradd --uid ${EXECUTOR_UID} --gid executor --groups isolate --create-home executor

COPY harness /app/harness
COPY runner /app/runner
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /app
ENV PATH=/opt/ghc/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "runner.api:app", "--uds", "/run/haskell-test-executor/executor.sock", "--no-access-log"]
