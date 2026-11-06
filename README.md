# Ejecutor seguro de Haskell

Servicio que evalúa envíos de Haskell para un juez automático: recibe el código
del estudiante, las definiciones iniciales del problema y una lista de tests
`(expresión, valor esperado)`, y devuelve el veredicto en JSON.

- Compila con GHC 9.10.3 y ejecuta en sandboxes de [isolate](https://github.com/ioi/isolate)
  (sin red, uid sin privilegios, límites de CPU, memoria y procesos por cgroups v2).
- El código del estudiante se compila en modo Safe Haskell y no puede contener pragmas.
- Se detiene en el primer test que falla y lo describe (salvo si es oculto).
- Todo va en un contenedor Docker sin red que escucha en un socket Unix.

Documentación relacionada:
- [DEVELOPMENT.md](DEVELOPMENT.md): pruebas, cambio de versión de GHC o de Ubuntu y rendimiento.
- [DEPLOYMENT.md](DEPLOYMENT.md): requisitos del servidor, instalación, seguridad del contenedor
  y actualización de la imagen (`docker/update.sh`).

## Estructura

```
harness/Judge.hs        ejecuta los tests dentro del programa compilado (solo depende de base)
runner/codegen.py       genera Student.hs, Defs.hs y Tests.hs a partir del envío
runner/diagnostics.py   interpreta los errores de compilación de GHC
runner/sandbox.py       sandboxes de isolate
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
  "limits": {"time_per_test_ms": 2000, "alloc_per_test_mb": 4000, "heap_mb": 200, "stack_mb": 64, "run_time_s": 10},
  "require_signatures": true
}
```

Solo `student_code` y `tests` son obligatorios. `expr` y `expected` son
expresiones Haskell de una línea. Si el estudiante redefine un nombre del Prelude,
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
{"status": "COMPILATION_ERROR", "source": "student", "code": "missing_signature",
 "signatures": [{"name": "doble", "line": 3, "column": 1}],
 "message": "solution.hs:3:1: error: missing type signature for doble"}
{"status": "INTERNAL_ERROR", "code": "internal_error", "message": "Internal executor error."}
```

- `TIME_LIMIT.reason`: `timeout` (tiempo de reloj del test), `allocation` (memoria
  reservada por el test, determinista), `cpu` o `wall` (límite total del proceso).
- `COMPILATION_ERROR.source`: `student` (errores en su código, con sus números de
  línea) o `tests` (su solución no tiene el tipo que espera el test `index`).
- `require_signatures` (por defecto `false`): toda definición global del estudiante debe
  tener firma de tipo (`-Werror=missing-signatures`; las de `where` y `let` no). Si falta
  alguna, la respuesta lleva el código `missing_signature` y, en `signatures`, el nombre
  (`null` si no se ha podido extraer), la línea y la columna de cada definición sin firma,
  en orden. El mensaje de GHC no se devuelve nunca porque incluye el tipo inferido, que
  es la respuesta de los problemas en que se pide escribir el tipo de una función. Si hay
  otros errores, se informa solo de ellos (GHC no comprueba las firmas hasta que el
  código compila).

### Códigos de mensaje

Los mensajes que genera el propio ejecutor (no GHC ni el programa del estudiante)
llevan un campo `code` estable y un `message` por defecto en inglés. El cliente
puede mostrar su propio texto traducido a partir de `code`; si no hay `code`, el
mensaje viene de GHC o del programa y se muestra tal cual.

| `code` | Veredicto | Datos | Significado |
|---|---|---|---|
| `pragma_not_allowed` | `COMPILATION_ERROR` | `line` | La solución contiene un pragma `{-# ... #-}` |
| `module_declaration_not_allowed` | `COMPILATION_ERROR` | `line` | La solución contiene `module ... where` |
| `missing_signature` | `COMPILATION_ERROR` | `signatures` | Con `require_signatures`, alguna definición global no tiene firma de tipo |
| `hidden_test_type_mismatch` | `COMPILATION_ERROR` | `test.index` | La solución no tiene el tipo que espera un test oculto |
| `compile_limits_exceeded` | `COMPILATION_ERROR` | — | GHC superó los límites de tiempo o memoria |
| `program_crashed` | `RUNTIME_ERROR` | `test` | El programa terminó sin informe (p. ej. por una señal) |
| `internal_error` | `INTERNAL_ERROR` | — | Fallo del sandbox o de la configuración (detalles en el log) |
- Errores de la petición: 422 (formato o límites fuera de rango), 400 (extensión no
  permitida, test de varias líneas…), 503 (cola llena).

`GET /health` devuelve versión de GHC, commit del ejecutor (`revision`, el de la imagen; `unknown`
si se construyó sin `--build-arg REVISION=…`), sandboxes ocupados, cola y estadísticas de la caché.

## Desarrollo y despliegue

```sh
python3 -m unittest discover -t . -s runner/tests     # pruebas en local
docker build -f docker/Dockerfile -t haskell-test-executor --build-arg REVISION=$(git rev-parse --short HEAD) .
```

Las pruebas dentro de la imagen y los requisitos para ejecutarlas en local están en
[DEVELOPMENT.md](DEVELOPMENT.md). En el servidor, el ejecutor es un servicio de systemd que
arranca el contenedor sin red y con los permisos mínimos que necesita isolate; se instala y se
actualiza (`sudo docker/update.sh`) como se explica en [DEPLOYMENT.md](DEPLOYMENT.md).

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
    "missing_signature": lambda r: "\n".join(
        _("Línea %(line)d: falta la firma de tipo de %(name)s.") % s for s in r["signatures"]
    ),
    "hidden_test_type_mismatch": lambda r: _("Tu solución no es compatible con un test oculto: revisa los tipos de tus funciones."),
    "compile_limits_exceeded": lambda r: _("La compilación ha excedido los límites de tiempo o memoria."),
    "program_crashed": lambda r: _("El programa terminó de forma inesperada."),
    "internal_error": lambda r: _("Error interno del juez. Inténtalo de nuevo más tarde."),
}

def mensaje(resultado: dict) -> str:
    texto = MENSAJES.get(resultado.get("code"))
    return texto(resultado) if texto else resultado.get("message", "")
```

## Licencia

[MIT](LICENSE)
