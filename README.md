# Ejecutor seguro de Haskell

Servicio que evalúa envíos de Haskell para un juez automático: recibe el código
del alumno, las definiciones iniciales del problema y una lista de tests
`(expresión, valor esperado)`, y devuelve el veredicto en JSON.

- Compila con GHC 9.10.3 y ejecuta en cajas de [isolate](https://github.com/ioi/isolate)
  (sin red, uid sin privilegios, límites de CPU, memoria y procesos por cgroups v2).
- El código del alumno se compila en modo Safe Haskell y no puede contener pragmas.
- Se detiene en el primer test que falla y lo describe (salvo si es oculto).
- Todo va en un contenedor Docker sin red que escucha en un socket Unix.

## Estructura

```
harness/Judge.hs        ejecuta los tests dentro del programa compilado (solo depende de base)
runner/codegen.py       genera Student.hs, Defs.hs y Tests.hs a partir del envío
runner/diagnostics.py   interpreta los errores de compilación de GHC
runner/sandbox.py       cajas de isolate
runner/judge.py         compilar + ejecutar + veredicto
runner/cache.py         Judge y Defs precompilados (por hash)
runner/api.py           servicio HTTP (FastAPI)
docker/                 imagen, entrypoint y unidad systemd
bench/                  benchmark de GHC y prueba de carga
```

## API

`POST /run`

```json
{
  "student_code": "suma :: [Int] -> Int\nsuma = foldr (+) 0\n",
  "definitions": "data Arbol a = Hoja | Nodo (Arbol a) a (Arbol a) deriving (Show, Eq)\n",
  "tests": [
    {"expr": "suma []", "expected": "0"},
    {"expr": "suma [1,2,3]", "expected": "6"},
    {"expr": "suma [1 .. 100000]", "expected": "5000050000", "hidden": true}
  ],
  "extensions": ["LambdaCase"],
  "limits": {"time_per_test_ms": 2000, "alloc_per_test_mb": 4000, "heap_mb": 200, "stack_mb": 64, "run_time_s": 10}
}
```

Solo `student_code` y `tests` son obligatorios. `expr` y `expected` son
expresiones Haskell de una línea. Si el alumno redefine un nombre del Prelude,
el test debe cualificarlo: `Student.sum [1,2]`.

Respuestas (siempre HTTP 200; el veredicto va en `status`):

```json
{"status": "OK", "passed": 3, "total": 3}
{"status": "WRONG_ANSWER", "passed": 1, "total": 3,
 "test": {"index": 2, "expr": "suma [1,2,3]", "expected": "6", "obtained": "1"}}
{"status": "RUNTIME_ERROR", "passed": 0, "total": 3,
 "test": {"index": 1, "expr": "suma []", "exception": "solution.hs:2:1-25: Non-exhaustive patterns in function suma\n"}}
{"status": "TIME_LIMIT", "passed": 2, "total": 3, "test": {"index": 3, "hidden": true, "reason": "timeout"}}
{"status": "MEMORY_LIMIT", "passed": 0, "total": 3, "test": {"index": 1, "expr": "suma []"}}
{"status": "COMPILATION_ERROR", "source": "student", "message": "solution.hs:3:29: error: ..."}
{"status": "COMPILATION_ERROR", "source": "tests", "message": "tests:2:24: error: ...", "test": {"index": 2, "expr": "suma \"abc\""}}
{"status": "COMPILATION_ERROR", "source": "student", "code": "pragma_not_allowed", "line": 1,
 "message": "solution.hs:1: pragmas ({-# ... #-}) are not allowed in the solution"}
{"status": "INTERNAL_ERROR", "code": "internal_error", "message": "Internal executor error."}
```

- `TIME_LIMIT.reason`: `timeout` (tiempo de reloj del test), `allocation` (memoria
  reservada por el test, determinista), `cpu` o `wall` (límite total del proceso).
- `COMPILATION_ERROR.source`: `student` (errores en su código, con sus números de
  línea) o `tests` (su solución no tiene el tipo que espera el test `index`).

### Códigos de mensaje

Los mensajes que genera el propio ejecutor (no GHC ni el programa del alumno)
llevan un campo `code` estable y un `message` por defecto en inglés. El cliente
puede mostrar su propio texto traducido a partir de `code`; si no hay `code`, el
mensaje viene de GHC o del programa y se muestra tal cual.

| `code` | Veredicto | Datos | Significado |
|---|---|---|---|
| `pragma_not_allowed` | `COMPILATION_ERROR` | `line` | La solución contiene un pragma `{-# ... #-}` |
| `module_declaration_not_allowed` | `COMPILATION_ERROR` | `line` | La solución contiene `module ... where` |
| `hidden_test_type_mismatch` | `COMPILATION_ERROR` | `test.index` | La solución no tiene el tipo que espera un test oculto |
| `compile_limits_exceeded` | `COMPILATION_ERROR` | — | GHC superó los límites de tiempo o memoria |
| `program_crashed` | `RUNTIME_ERROR` | `test` | El programa terminó sin informe (p. ej. por una señal) |
| `internal_error` | `INTERNAL_ERROR` | — | Fallo del sandbox o de la configuración (detalles en el log) |
- Errores de la petición: 422 (formato o límites fuera de rango), 400 (extensión no
  permitida, test de varias líneas…), 503 (cola llena).

`GET /health` devuelve versión de GHC, cajas ocupadas, cola y estadísticas de la caché.

## Desarrollo y pruebas

```sh
python3 -m unittest discover -t . -s runner/tests     # en local (sin fastapi se omiten las de la API)
bash harness/run_tests.sh                             # casos del harness

docker build -f docker/Dockerfile -t haskell-test-executor .
docker run --rm --network none --cap-drop ALL --cap-add SYS_ADMIN --cap-add NET_ADMIN \
  --cap-add SETUID --cap-add SETGID --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SYS_CHROOT --security-opt apparmor=unconfined \
  haskell-test-executor python3 -m unittest discover -t . -s runner/tests
```

En local hace falta `ghc` y, para las pruebas del sandbox, `isolate` instalado y
pertenecer al grupo `isolate`.

## Despliegue en el servidor

Requisitos del host (los contenedores comparten su kernel):

1. **cgroups v2**: añadir `systemd.unified_cgroup_hierarchy=1` a `GRUB_CMDLINE_LINUX`
   en `/etc/default/grub`, `sudo update-grub` y reiniciar. Comprobar con
   `stat -fc %T /sys/fs/cgroup` → `cgroup2fs`.
2. **Kernel ≥ 5.7** (isolate usa `clone3` con `CLONE_INTO_CGROUP`). En Ubuntu 20.04:
   `sudo apt install linux-generic-hwe-20.04` y reiniciar.

Instalación:

```sh
# en la máquina de desarrollo
docker build -f docker/Dockerfile -t haskell-test-executor .
docker save haskell-test-executor | gzip | ssh servidor 'gunzip | docker load'

# en el servidor
sudo groupadd --system haskell-test-executor
sudo usermod -aG haskell-test-executor <usuario de Django>
sudo cp docker/haskell-test-executor.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now haskell-test-executor
curl --unix-socket /run/haskell-test-executor/executor.sock http://executor/health
```

El socket solo es accesible para el grupo `haskell-test-executor`. El contenedor corre sin red
(`--network none`) y, en lugar de `--privileged`, solo con los permisos (*capabilities*) que
necesita isolate para crear sus cajas: `SYS_ADMIN`, `NET_ADMIN`, `SETUID`, `SETGID`, `CHOWN`,
`DAC_OVERRIDE`, `FOWNER` y `SYS_CHROOT`. Así, el root del contenedor no ve los discos del host,
no puede cargar módulos ni cambiar parámetros del kernel, y se mantiene el filtro seccomp de
Docker. AppArmor se desactiva para el contenedor porque su perfil por defecto impide los
montajes que hacen el entrypoint e isolate. Dentro, el servicio se ejecuta como el usuario
`executor` (uid 6000, que no debe existir en el host).

## Actualizar la imagen

Los paquetes de Ubuntu (gcc, Python, FastAPI…) e isolate se instalan con `apt` al construir
la imagen, así que para recibir sus parches de seguridad basta con reconstruirla de vez en
cuando (por ejemplo, una vez al mes o tras un aviso de seguridad importante):

```sh
# en la máquina de desarrollo: --pull descarga la última imagen base de Ubuntu y
# --no-cache evita reutilizar capas antiguas (si no, apt no instalaría nada nuevo)
docker build --pull --no-cache -f docker/Dockerfile -t haskell-test-executor .
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

**Cambiar de versión de GHC**: en `docker/Dockerfile`, actualizar `GHC_VERSION` y
`GHC_SHA256` (en el `SHA256SUMS` de `https://downloads.haskell.org/~ghc/<versión>/`, la línea
del fichero `GHC_BINDIST`), reconstruir y pasar también `bash harness/run_tests.sh` en local
con la misma versión.

**Cambiar de versión de Ubuntu**: actualizar `UBUNTU` y, en la línea del repositorio de
isolate, `Suites: <nombre en clave>-isolate`.

## Cliente en Django

```python
import httpx

_client = httpx.Client(
    transport=httpx.HTTPTransport(uds="/run/haskell-test-executor/executor.sock"),
    base_url="http://haskell-test-executor",
    timeout=60,
)

def evaluar(envio: dict) -> dict:
    r = _client.post("/run", json=envio)
    r.raise_for_status()
    return r.json()
```

Textos traducidos para los mensajes con `code` (con `gettext` de Django):

```python
from django.utils.translation import gettext as _

MENSAJES = {
    "pragma_not_allowed": lambda r: _("Línea %(line)d: no se permiten pragmas ({-# ... #-}) en la solución.") % r,
    "module_declaration_not_allowed": lambda r: _("Línea %(line)d: no incluyas la declaración 'module'.") % r,
    "hidden_test_type_mismatch": lambda r: _("Tu solución no es compatible con un test oculto: revisa los tipos de tus funciones."),
    "compile_limits_exceeded": lambda r: _("La compilación ha excedido los límites de tiempo o memoria."),
    "program_crashed": lambda r: _("El programa terminó de forma inesperada."),
    "internal_error": lambda r: _("Error interno del juez. Inténtalo de nuevo más tarde."),
}

def mensaje(resultado: dict) -> str:
    texto = MENSAJES.get(resultado.get("code"))
    return texto(resultado) if texto else resultado.get("message", "")
```

## Rendimiento (máquina de desarrollo, 3 cajas)

Un envío típico tarda ~0,3 s (compilar ~0,12 s, enlazar con lld ~0,1 s, ejecutar
~0,02 s). 30 envíos simultáneos se resuelven en ~3 s.

## Licencia

[MIT](LICENSE)
