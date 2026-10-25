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
 "test": {"index": 1, "expr": "suma []", "exception": "solucion.hs:2:1-25: Non-exhaustive patterns in function suma\n"}}
{"status": "TIME_LIMIT", "passed": 2, "total": 3, "test": {"index": 3, "hidden": true, "reason": "timeout"}}
{"status": "MEMORY_LIMIT", "passed": 0, "total": 3, "test": {"index": 1, "expr": "suma []"}}
{"status": "COMPILATION_ERROR", "source": "student", "message": "solucion.hs:3:29: error: ..."}
{"status": "COMPILATION_ERROR", "source": "tests", "message": "tests:2:24: error: ...", "test": {"index": 2, "expr": "suma \"abc\""}}
{"status": "INTERNAL_ERROR", "message": "Error interno del juez."}
```

- `TIME_LIMIT.reason`: `timeout` (tiempo de reloj del test), `allocation` (memoria
  reservada por el test, determinista), `cpu` o `wall` (límite total del proceso).
- `COMPILATION_ERROR.source`: `student` (errores en su código, con sus números de
  línea) o `tests` (su solución no tiene el tipo que espera el test `index`).
- Errores de la petición: 422 (formato o límites fuera de rango), 400 (extensión no
  permitida, test de varias líneas…), 503 (cola llena).

`GET /health` devuelve versión de GHC, cajas ocupadas, cola y estadísticas de la caché.

## Desarrollo y pruebas

```sh
python3 -m unittest discover -t . -s runner/tests     # en local (sin fastapi se omiten las de la API)
bash harness/run_tests.sh                             # casos del harness

docker build -f docker/Dockerfile -t haskell-judge .
docker run --rm --privileged haskell-judge python3 -m unittest discover -t . -s runner/tests
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
docker build -f docker/Dockerfile -t haskell-judge .
docker save haskell-judge | gzip | ssh servidor 'gunzip | docker load'

# en el servidor
sudo groupadd --system haskell-judge
sudo usermod -aG haskell-judge <usuario de Django>
sudo cp docker/haskell-judge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now haskell-judge
curl --unix-socket /run/haskell-judge/judge.sock http://judge/health
```

El socket solo es accesible para el grupo `haskell-judge`. El contenedor corre con
`--privileged` (lo necesita isolate) y `--network none`. Dentro, el servicio se
ejecuta como el usuario `judge` (uid 6000, que no debe existir en el host).

## Cliente en Django

```python
import httpx

_client = httpx.Client(
    transport=httpx.HTTPTransport(uds="/run/haskell-judge/judge.sock"),
    base_url="http://haskell-judge",
    timeout=60,
)

def evaluar(envio: dict) -> dict:
    r = _client.post("/run", json=envio)
    r.raise_for_status()
    return r.json()
```

## Rendimiento (máquina de desarrollo, 3 cajas)

Un envío típico tarda ~0,3 s (compilar ~0,12 s, enlazar con lld ~0,1 s, ejecutar
~0,02 s). 30 envíos simultáneos se resuelven en ~3 s.

## Licencia

[MIT](LICENSE)
