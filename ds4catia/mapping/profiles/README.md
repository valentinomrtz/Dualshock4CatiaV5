# Perfiles de mapeo por defecto

Este directorio contiene los perfiles `Profile` (ver `ds4catia/mapping/schema.py`)
que se instalan por defecto con la aplicación. Cada archivo `*.json` debe cargar
sin error con `load_profile(path)`. El test `tests/test_default_profiles.py`
verifica esto automáticamente para todos los perfiles de este directorio.

Como `Profile` tiene `model_config = {"extra": "forbid"}`, no se pueden añadir
campos de metadatos extra dentro del propio JSON del perfil (p. ej. para marcar
un placeholder sin verificar). Por eso ese estado se documenta aquí, fuera del
esquema estricto, en vez de dentro de cada JSON.

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

Los `command_id` usados en `part_design.json`, `assembly_design.json`,
`sketcher.json`, `drafting.json` y `generative_shape_design.json` (p. ej.
`"PartDesign.Pad"`, `"Assembly.CoincidenceConstraint"`, `"Sketcher.Line"`,
`"Drafting.Dimension"`, `"GSD.Extrude"`) son nombres de comando **plausibles
pero no verificados contra una instalación real de CATIA V5**. La investigación
del proyecto (`catia_automation_api.md`) confirma que los identificadores de
comando exactos deben descubrirse en la instalación real vía el menú
View > "Command List…" (o Tools > Customize > Commands), y que `StartCommand`
es el mecanismo correcto para invocarlos — pero no aporta un listado verbatim
de IDs por comando. Antes de usar estos perfiles en producción, el usuario
debería:

1. Abrir "Command List…" en cada workbench relevante.
2. Confirmar/corregir el `command_id` de cada binding `catia_command` en los
   JSON de este directorio.

Los comandos de cámara (`catia_camera`, operaciones `rotate`/`translate`/`zoom`)
y de teclado/ratón (`keyboard`, `mouse_move`, `mouse_button`) no dependen de
`command_id` y no requieren esta verificación.
