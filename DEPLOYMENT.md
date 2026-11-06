# Despliegue

El ejecutor corre en el mismo servidor que el juez, como un contenedor Docker gestionado por
systemd (`docker/haskell-test-executor.service`) que escucha en el socket Unix
`/run/haskell-test-executor/executor.sock`.

## Requisitos del host

Los contenedores comparten el kernel del host:

1. **cgroups v2**: añadir `systemd.unified_cgroup_hierarchy=1` a `GRUB_CMDLINE_LINUX`
   en `/etc/default/grub`, `sudo update-grub` y reiniciar. Comprobar con
   `stat -fc %T /sys/fs/cgroup` → `cgroup2fs`.
2. **Kernel ≥ 5.7** (isolate usa `clone3` con `CLONE_INTO_CGROUP`). En Ubuntu 20.04:
   `sudo apt install linux-generic-hwe-20.04` y reiniciar.
3. **Docker** con **BuildKit** (`docker buildx version`; en Ubuntu, el paquete `docker-buildx`,
   o `docker-buildx-plugin` si Docker viene del repositorio de Docker). Sin él, `docker build`
   usa el constructor antiguo (obsoleto), más lento y sin `--no-cache-filter`.

## Instalación

En los ejemplos, `<directorio>` es un clon del repositorio en el servidor, propiedad de root
(quien pueda modificar ese código decide qué se ejecuta en el contenedor), y `<usuario de Django>`
el usuario del sistema que ejecuta el juez.

```sh
sudo git clone https://github.com/emartinm/haskell-test-executor.git <directorio>
cd <directorio>
sudo docker build -f docker/Dockerfile -t haskell-test-executor --build-arg REVISION=$(git rev-parse --short HEAD) .
sudo groupadd --system haskell-test-executor
sudo usermod -aG haskell-test-executor <usuario de Django>
sudo cp docker/haskell-test-executor.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now haskell-test-executor
curl --unix-socket /run/haskell-test-executor/executor.sock http://executor/health
```

La imagen también se puede construir en otra máquina y llevarla al servidor (ver abajo).

## Seguridad

El socket solo es accesible para el grupo `haskell-test-executor`. El contenedor corre sin red
(`--network none`) y, en lugar de `--privileged`, solo con los permisos (*capabilities*) que
necesita isolate para crear sus sandboxes: `SYS_ADMIN`, `NET_ADMIN`, `SETUID`, `SETGID`, `CHOWN`,
`DAC_OVERRIDE`, `FOWNER` y `SYS_CHROOT`. Así, el root del contenedor no ve los discos del host,
no puede cargar módulos ni cambiar parámetros del kernel, y se mantiene el filtro seccomp de
Docker. AppArmor se desactiva para el contenedor porque su perfil por defecto impide los
montajes que hacen el entrypoint e isolate. Dentro, el servicio se ejecuta como el usuario
`executor` (uid 6000, que no debe existir en el host).

## Actualizar la imagen

Los paquetes de Ubuntu (gcc, Python, FastAPI…) e isolate se instalan con `apt` al construir
la imagen, así que para recibir sus parches de seguridad basta con reconstruirla de vez en
cuando (por ejemplo, una vez al mes o tras un aviso de seguridad importante), además de cuando
cambia el código.

Lo más cómodo es construirla en el propio servidor con `docker/update.sh`, desde el clon del
repositorio:

```sh
sudo <directorio>/docker/update.sh
```

El script hace `git pull`, construye la imagen nueva con la última imagen base de Ubuntu y sin
caché en la etapa final (`--pull --no-cache-filter final`: los paquetes de Ubuntu e isolate
siempre se actualizan, y GHC, cuya versión está fijada, sale de la caché; unos 2-3 minutos, u
8 si hay que construir GHC porque no está en la caché o porque ha cambiado su versión o la
imagen base). Sin BuildKit usa `--pull --no-cache` (siempre unos 8 minutos). Luego pasa los
tests dentro de la imagen y solo entonces la
pone en servicio: la etiqueta como `latest` y con su commit, deja la anterior como `rollback`,
reinicia el servicio y comprueba que evalúa un envío. Si no funciona, vuelve solo a la imagen
anterior. Mientras construye y pasa los tests, el servicio sigue con la imagen anterior.

- Conserva las imágenes de los 3 últimos commits (`KEEP`).
- Avisa si `docker/haskell-test-executor.service` ha cambiado respecto al instalado (no lo
  copia: hay que revisarlo).
- Con `--no-pull`, construye el código que ya hay en el clon.
- Si dentro de `docker build` no se resuelven nombres (DNS):
  `BUILD_OPTS="--pull --no-cache-filter final --network host"`.
  Antes, comprueba si los contenedores tienen salida a Internet: si
  `docker run --rm ubuntu:26.04 getent hosts security.ubuntu.com` se queda colgado pero con
  `--network host` responde, no es un problema de DNS sino de la red de Docker, que ha perdido
  sus reglas de reenvío y NAT (p. ej., tras un cambio de red o al recargar el cortafuegos con
  Docker en marcha). Se arregla con `sudo systemctl restart docker`, que las vuelve a crear
  (y para los contenedores, que hay que arrancar otra vez).
- Se puede programar con cron (como root); termina con error si algo falla.

También se puede construir en otra máquina y llevar la imagen al servidor:

```sh
# en la máquina de desarrollo: --pull descarga la última imagen base de Ubuntu y
# --no-cache-filter final rehace la etapa final (si no, apt no instalaría nada nuevo)
# pero reutiliza GHC de la caché
docker build --pull --no-cache-filter final -f docker/Dockerfile -t haskell-test-executor --build-arg REVISION=$(git rev-parse --short HEAD) .
docker run --rm --network none --cap-drop ALL --cap-add SYS_ADMIN --cap-add NET_ADMIN \
  --cap-add SETUID --cap-add SETGID --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SYS_CHROOT --security-opt apparmor=unconfined \
  haskell-test-executor python3 -m unittest discover -t . -s runner/tests
docker save haskell-test-executor | gzip | ssh servidor 'gunzip | docker load'

# en el servidor
sudo systemctl restart haskell-test-executor
curl --unix-socket /run/haskell-test-executor/executor.sock http://executor/health
sudo docker image prune -f        # borra la imagen anterior, que queda sin etiqueta
```

Mientras se reinicia el servicio (unos segundos), los envíos fallan con un error de conexión:
mejor hacerlo en un momento sin actividad.
