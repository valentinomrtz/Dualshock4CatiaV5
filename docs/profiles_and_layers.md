# Perfiles, bindings y capas

ds4catia mapea los controles del DualShock 4 a acciones de CATIA V5 con un
modelo de **tres niveles**: `Binding` → `Profile` → `Layer`. Están definidos
en `ds4catia/mapping/schema.py` y validados contra el JSON Schema publicado
en `ds4catia/mapping/profile.schema.json`.

## Binding: una entrada física → una acción

Un `Binding` conecta un control físico del mando con una acción destino
(`ActionTarget`), con modificadores opcionales para suavizar/afinar la
respuesta:

```json
{
  "id": "base.left_stick.orbit",
  "input": { "device": "ds4", "control": "left_stick" },
  "target": { "type": "catia_camera", "operation": "rotate" },
  "analog": { "deadzone": 0.15, "sensitivity": 1.0, "curve": "ease_out_quad" }
}
```

(ejemplo real tomado de `ds4catia/mapping/profiles/base.json`)

- **`input.control`**: nombre del control físico (`cross`, `circle`,
  `square`, `triangle`, `l1`, `r1`, `l2`/`l2_digital`, `r2`/`r2_digital`,
  `l3`, `r3`, `share`, `options`, `ps`, `dpad`, `left_stick`, `right_stick`,
  `touchpad_tap`, `touchpad_swipe`, `touchpad_click`, `gyro_x`/`gyro_y`/`gyro_z`).
- **`target.type`**: una de 9 acciones posibles — `catia_command`,
  `catia_camera` (rotate/translate/zoom), `catia_selection`
  (add/clear/search), `catia_macro`, `keyboard`, `mouse_move`,
  `mouse_button`, `layer_control` (ver más abajo) y `app_control`
  (pausar/reanudar/cambiar de perfil).
- **`analog`** (`AnalogModifiers`): `deadzone`, `anti_deadzone`,
  `sensitivity`, y una `curve` de respuesta (`linear`, `quadratic`, `cubic`,
  `ease_out_quad`, `ease_out_cubic`, `custom_bezier`) para sticks, gatillos
  analógicos y giroscopio.
- **`digital`** (`DigitalModifiers`): `tap_ms`/`hold_ms`, para distinguir una
  pulsación corta ("tap") de una mantenida ("hold") en el mismo botón.

## Profile: un conjunto de bindings por contexto

Un `Profile` agrupa bindings para un "modo" de uso. El proyecto incluye
(`ds4catia/mapping/profiles/`):

- `base.json` — perfil global, activo cuando ningún otro aplica.
- `part_design.json`, `assembly_design.json`, `sketcher.json`,
  `drafting.json`, `generative_shape_design.json` — uno por workbench de
  CATIA V5.

Cada perfil específico de workbench declara `auto_activate`:

```json
"auto_activate": { "trigger": "catia_workbench_id", "match": "PrtCfg" }
```

El daemon (`Ds4CatiaDaemon`) sondea periódicamente
`Application.GetWorkbenchId()` por COM y, si el valor coincide de forma
**estable** (varias lecturas consecutivas iguales — ver "debounce" en
[`manual_verification_checklist.md`](manual_verification_checklist.md)) con
el `match` de algún perfil, cambia automáticamente a ese perfil.

> ⚠️ **Importante**: de los IDs de workbench usados en los perfiles por
> defecto, solo `"PrtCfg"` (Part Design) está confirmado por la
> investigación del proyecto. Los demás (`AsmCfg`, `SktCfg`, `CfgCfg`,
> `GSDCfg`) son **estimaciones sin verificar** que casi seguro habrá que
> corregir contra tu instalación real de CATIA. El procedimiento exacto para
> verificarlos y corregir los JSON está en
> [`ds4catia/mapping/profiles/README.md`](../ds4catia/mapping/profiles/README.md)
> — léelo antes de confiar en el cambio automático de perfil.

De la misma forma, los `command_id` usados en los bindings `catia_command`
(p. ej. `"PartDesign.Pad"`) son nombres de comando plausibles pero no
verificados contra una instalación real; confírmalos vía el menú "Command
List…" de CATIA, como también se explica en ese mismo README.

## Layer: un "modo shift" temporal

Una `Layer` es una superposición de bindings activada manteniendo pulsado
(o alternando) un control, que **solo sobreescribe los controles que lista
explícitamente** — cualquier otro control sigue usando el binding del
perfil base. Ejemplo real (`part_design.json`):

```json
{
  "id": "pd.l1.enter_constraint_layer",
  "input": { "device": "ds4", "control": "l1" },
  "target": { "type": "layer_control", "layer_id": "pd.l1-sketch-tools", "mode": "hold" }
}
```

```json
{
  "layer_id": "pd.l1-sketch-tools",
  "activation": { "trigger": { "device": "ds4", "control": "l1" }, "mode": "hold" },
  "clears_on_profile_switch": true,
  "overrides": [
    {
      "id": "pd.l1.cross.constraint_coincident",
      "input": { "device": "ds4", "control": "cross" },
      "target": { "type": "catia_command", "command_id": "Sketcher.Constraint.Coincident" }
    }
  ]
}
```

Mientras se mantiene `L1`, el botón `cross` (normalmente "Sketcher.NewSketch"
en este perfil) pasa a disparar `Sketcher.Constraint.Coincident`; al soltar
`L1`, vuelve a su binding normal. **Cambiar de perfil siempre limpia todas
las capas activas**, sin importar el valor de `clears_on_profile_switch` de
cada una (replica la semántica de los *action set layers* de Steam Input).
Si dos capas activas sobreescriben el mismo control, gana la activada más
recientemente.

## Edición manual del JSON

Cada archivo de perfil debe cargar sin error con
`ds4catia.mapping.schema.load_profile(path)`, que valida contra el mismo
modelo pydantic que genera `profile.schema.json`. Si editas un perfil a mano
y algo no valida (p. ej. un `deadzone` fuera de `[0, 1]`, o un nombre de
`control` desconocido), obtendrás un `ProfileValidationError` con un mensaje
legible de qué campo falló — tanto desde la línea de comandos como desde el
editor de la GUI (ver [`remapping_gui.md`](remapping_gui.md)), que es la vía
recomendada para la mayoría de los usuarios.
