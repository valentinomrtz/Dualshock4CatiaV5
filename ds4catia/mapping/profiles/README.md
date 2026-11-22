# Perfiles de mapeo por defecto

Este directorio contiene los perfiles `Profile` (ver `ds4catia/mapping/schema.py`)
que se instalan por defecto con la aplicación. Cada archivo `*.json` debe cargar
sin error con `load_profile(path)`. El test `tests/test_default_profiles.py`
verifica esto automáticamente para todos los perfiles de este directorio.

Como `Profile` tiene `model_config = {"extra": "forbid"}`, no se pueden añadir
campos de metadatos extra dentro del propio JSON del perfil (p. ej. para marcar
un placeholder sin verificar). Por eso ese estado se documenta aquí, fuera del
esquema estricto, en vez de dentro de cada JSON.

## Binding conocido como no funcional: `base.l1.cross.add_to_selection`

El binding `cross` dentro de la capa `base.l1-shift` en `base.json` usa
`catia_selection` con `operation: "add"` y sin `query`. La API COM real de
`Selection.Add` espera una **referencia a un objeto** de CATIA, no una
cadena de texto (ver el docstring de `ComCatiaBackend.selection_add` en
`ds4catia/catia/com_backend.py`) — así que este binding, tal como está
definido, muy probablemente no haga nada útil y aparezca en `--verbose`
como un `catia_error` (type mismatch de COM). No se ha corregido a ciegas
porque arreglarlo bien requiere decidir una UX real (¿seleccionar por texto
con `Search`, que reemplaza la selección entera, en vez de añadir un
objeto concreto?) que no se puede validar sin una sesión de CATIA real.

## Binding sospechoso por diseño (no solo por el nombre): `base.dpad.standard_views`

El D-pad en `base.json` está mapeado a `catia_command` con `command_id:
"View.NamedViews"`. Además de que ese `command_id` concreto es, como todos
los `catia_command` salvo los dos confirmados arriba, un placeholder sin
verificar, hay un problema de diseño más de fondo: "Named Views…" es
típicamente un comando que **abre un diálogo interactivo** (un selector de
vistas con nombre) — y `StartCommand` solo es seguro para comandos de un
solo paso, no interactivos (ver el docstring de
`CatiaBackend.start_command` y la guía de soporte de IBM citada en la
investigación: "interactions you could add after in your macro will not
work"). Aunque se corrija el nombre exacto del comando, si de verdad abre
un diálogo, `StartCommand` no lo manejará bien desde un binding pensado
para dispararse repetidamente con el mando.

Antes de confiar en este binding, considera reemplazarlo por un preset de
vista de un solo paso (p. ej. el equivalente real a "Iso View"/"Front
View"/"Top View" en lugar de un selector de vistas con nombre), una vez
verificado su `command_id` real con el método de grabar una macro descrito
más abajo. También nótese que, tal como está definido el control `dpad` en
el esquema (`ds4catia/mapping/engine.py`, `_read_digital_raw`), las 4
direcciones del D-pad se tratan como un único botón combinado — no hay
forma hoy de asignar una acción distinta a cada dirección (arriba/abajo/
izquierda/derecha) con un solo binding de `dpad`; eso sería una mejora de
funcionalidad futura, no un bug de esta versión.

## Estado de `auto_activate.match` por perfil

| Perfil (`profile_id`)       | Archivo                         | `auto_activate.match` | Estado                              | Qué debe hacer el usuario |
|------------------------------|----------------------------------|------------------------|--------------------------------------|----------------------------|
| `base`                       | `base.json`                     | *(ninguno, `auto_activate: null`)* | N/A — perfil global, no se auto-activa por workbench | — |
| `part_design`                | `part_design.json`              | `PrtCfg`               | **CONFIRMADO** por investigación (`research_notes/.../catia_automation_api.md`, confirmado también por el reporte final) | Ninguna acción necesaria, pero conviene verificar una vez en hardware real que `Application.GetWorkbenchId()` devuelve efectivamente `"PrtCfg"` en la instalación del usuario (la versión de CATIA puede variar). |
| `assembly_design`            | `assembly_design.json`          | `AsmCfg`               | **PLACEHOLDER a verificar — VERIFY-ON-HARDWARE**. No confirmado por la investigación del proyecto; es una estimación razonada basada en el nombre de clase CAA típicamente documentado en la comunidad para Assembly Design. | Abrir CATIA V5, activar el workbench "Assembly Design", y en el editor de macros (VBA) ejecutar `MsgBox CATIA.Application.GetWorkbenchId()` (o equivalente en pycatia: `catia.application.workbench_id`). Si el valor no es `"AsmCfg"`, corregir `auto_activate.match` en `assembly_design.json`. |
| `sketcher`                   | `sketcher.json`                 | `SktCfg`               | **PLACEHOLDER a verificar — VERIFY-ON-HARDWARE**. No confirmado por la investigación; estimación razonada (nombre de clase CAA típico para Sketcher). | Igual que arriba: entrar en modo Sketcher sobre cualquier boceto y comprobar `GetWorkbenchId()`. Corregir el JSON si difiere. |
| `drafting`                   | `drafting.json`                 | `CfgCfg`               | **PLACEHOLDER a verificar — VERIFY-ON-HARDWARE**. No confirmado por la investigación; estimación razonada (candidato típico para el workbench de Drafting/Generative Drafting, con baja confianza — es el ID más incierto de la lista). | Abrir un documento `.CATDrawing`, comprobar `GetWorkbenchId()` y corregir el JSON si no coincide. |
| `generative_shape_design`    | `generative_shape_design.json`  | `GSDCfg`               | **PLACEHOLDER a verificar — VERIFY-ON-HARDWARE**. No confirmado por la investigación; estimación razonada. **Importante:** la investigación sí confirma que `"GPSCfg"` corresponde a *Generative Structural Analysis*, NO a Generative Shape Design — no reutilizar ese ID aquí por error. | Activar el workbench "Generative Shape Design" sobre un `.CATPart`, comprobar `GetWorkbenchId()` y corregir el JSON si no coincide. |

## Cómo verificar `auto_activate.match` en CATIA real

1. Abrir CATIA V5 con un documento del tipo adecuado (Part para Part Design/GSD,
   Product para Assembly Design, un boceto activo para Sketcher, Drawing para
   Drafting).
2. Activar manualmente el workbench correspondiente desde el menú/barra de
   workbenches.
3. Ejecutar, desde una macro VBA o desde `pycatia`/`win32com` en Python:
   - VBA: `MsgBox CATIA.Application.GetWorkbenchId()`
   - Python (`win32com`): `catia.Application.GetWorkbenchId()` tras conectar
     con `win32com.client.Dispatch("CATIA.Application")`.
4. Comparar el valor devuelto con el campo `auto_activate.match` del perfil
   correspondiente en este directorio.
5. Si no coincide, editar ese campo en el JSON (sigue siendo un `Profile`
   válido mientras sea una cadena no vacía: `load_profile()` no restringe los
   valores posibles de `match` más allá de ser `str`).

## Nota sobre los `command_id` de `catia_command`

**Hallazgo confirmado durante la verificación manual** (un diálogo real de
CATIA mostró "Unknown command: View.Isometric" al probar el perfil base):
los `command_id` reales de `StartCommand` en CATIA V5 **no usan un formato de
espacio de nombres con puntos** como `"View.FitAll"` o `"Edit.Undo"` — son
cadenas de texto simples, normalmente muy parecidas (o idénticas) al nombre
que se ve en pantalla, como `"Fit All In"` o `"Undo"`. Esto se confirmó
buscando ejemplos reales de macros (`CATIA.StartCommand "Fit All In"`,
`CATIA.StartCommand "Undo"`) en foros de CATIA (eng-tips.com).

En base a esto:
- `base.json` → `"base.square.fit_all"` ya usa `"Fit All In"` (confirmado).
- `part_design.json`, `assembly_design.json`, `drafting.json`,
  `generative_shape_design.json`, `sketcher.json` → el binding de "Undo" ya
  usa `"Undo"` (confirmado).
- **El resto de `command_id`** en esos mismos archivos (p. ej.
  `"PartDesign.Pad"`, `"Assembly.CoincidenceConstraint"`, `"Sketcher.Line"`,
  `"Drafting.Dimension"`, `"GSD.Extrude"`, `"Edit.Update"`,
  `"Edit.UpdateAll"`, `"View.Isometric"`, `"View.NormalView"`,
  `"View.NamedViews"`) **siguen siendo placeholders con el formato antiguo,
  casi con toda seguridad incorrectos** — no se corrigieron todos porque no
  hay evidencia directa de la cadena exacta para cada uno, y reemplazar un
  placeholder por otro sin verificar no aporta nada.

### Cómo obtener el `command_id` real de cualquier comando (rápido)

El método más fiable y rápido, más que buscar a mano en "Tools > Customize >
Commands" uno por uno:

1. En CATIA, **Tools (Herramientas) > Macro > Start Recording** (grabar macro).
2. Ejecuta manualmente, con el ratón, la acción que quieras mapear (p. ej.
   clic en el botón "Fit All", o "Iso View", o "Undo").
3. **Stop Recording** (detener grabación) y guarda la macro.
4. Abre la macro en el editor VBA (Alt+F11 o Tools > Macro > Macros > Edit) —
   verás una línea literal `CATIA.StartCommand "..."` con la cadena EXACTA
   que usa tu instalación. Copia esa cadena tal cual al `command_id` del JSON.

Repite esto por cada comando de los perfiles que quieras usar. Los comandos
de cámara (`catia_camera`, operaciones `rotate`/`translate`/`zoom`) y de
teclado/ratón (`keyboard`, `mouse_move`, `mouse_button`) no dependen de
`command_id` y no requieren esta verificación.
