# Checklist de verificación manual (Windows + CATIA V5 + DS4 real)

Este proyecto se desarrolló en un contenedor Linux sin Windows, sin CATIA V5
y sin un mando DualShock 4 físico. Toda la lógica pura (parseo de reportes
HID, el motor de mapeo, el modelo de perfiles, el enrutado de acciones) tiene
tests automatizados que pasan (293 tests al momento de escribir esto), pero
**el código Windows-only nunca se ha ejecutado contra hardware o CATIA
reales**: la lectura HID real vía `hidapi`, la automatización COM vía
`pywin32`, la inyección `SendInput`, y el comportamiento visual real de la
GUI/bandeja.

Esta checklist es la forma de cerrar esa brecha. Ejecútala en orden la
primera vez que tengas disponible un PC Windows con CATIA V5 instalado y un
DS4 a mano.

## 1. Conexión COM

Con CATIA V5 abierto, desde un intérprete Python con `pywin32` instalado:

```python
import win32com.client
app = win32com.client.Dispatch("CATIA.Application")
print(app.Visible)
```

Debe conectar sin error. Si falla, confirma que CATIA V5 está realmente
corriendo y que la versión instalada expone la Automation API estándar.

## 2. Capturar los IDs de workbench reales

Solo `"PrtCfg"` (Part Design) está confirmado por la investigación del
proyecto. Para cada perfil en `ds4catia/mapping/profiles/` (Assembly Design,
Sketcher, Drafting, Generative Shape Design), activa ese workbench en CATIA
y ejecuta `Application.GetWorkbenchId()` (ver el procedimiento detallado en
[`ds4catia/mapping/profiles/README.md`](../ds4catia/mapping/profiles/README.md)).
Corrige `auto_activate.match` en el JSON correspondiente si no coincide con
el placeholder actual.

## 3. Lectura HID cruda por USB

```bash
ds4catia --list-devices
```

Debe listar el mando (VID `0x054C`, PID `0x05C4` o `0x09CC`). Luego:

```bash
ds4catia --dry-run --headless
```

y confirma (vía logs, o usando el panel dry-run de la GUI) que botones,
sticks y gatillos analógicos se leen con valores correctos al manipular el
mando conectado por USB.

## 4. Handshake y lectura extendida por Bluetooth

Empareja el mando por Bluetooth. Confirma que, sin el *handshake* de modo
extendido, los reportes llegan en modo básico (sin touchpad/gyro), y que
tras `DS4Device.perform_bt_extended_handshake()` empiezan a llegar reportes
con `report_id = 0x11` que sí incluyen touchpad y giroscopio.

## 5. Sanity de touchpad y giroscopio/acelerómetro

Con el mando quieto sobre una superficie plana, confirma que gyro/accel
están cerca de cero/de la constante de gravedad según el eje, y que arrastrar
un dedo por el touchpad produce coordenadas dentro del rango esperado del
panel (~1920×942). Si `validate_report_sanity()` (en
`ds4catia/hid/ds4_report.py`) emite advertencias con datos que a simple
vista parecen correctos, los offsets usados pueden necesitar corrección
contra `psdevwiki.com`/`eleccelerator.com` (no accesibles desde el entorno
de desarrollo de este proyecto).

## 6. Rumble y luz de estado

Envía un reporte de salida con rumble y un color de luz de estado
distintos de cero (`DS4Device.set_rumble_and_light(...)`). Si el mando no
vibra o la luz no cambia de color, el layout exacto de ese reporte de salida
(marcado `# VERIFY-ON-HARDWARE` en el código) necesita corregirse contra las
mismas fuentes comunitarias de ingeniería inversa.

## 7. Una acción COM discreta

Con un binding mapeado a `catia_command` con `command_id` de un comando de
un solo paso (p. ej. "Fit All In" → `View.FitAll`), pulsa el control
correspondiente con CATIA en foco y confirma que la vista se ajusta.

## 8. `SendInput` con foco real

Con un binding `mouse_move`/`keyboard` y la ventana de CATIA realmente en
foco (no solo visible), confirma que el movimiento/tecleo llega de verdad al
viewport 3D.

## 9. UIPI con CATIA elevado

Vuelve a abrir CATIA "Ejecutar como administrador" mientras ds4catia sigue
corriendo como usuario normal. Confirma que:
- Los bindings `catia_command`/`catia_camera`/`catia_selection` (vía COM)
  **siguen funcionando**.
- Los bindings `keyboard`/`mouse_move`/`mouse_button` (vía `SendInput`)
  **dejan de funcionar silenciosamente**, sin que ds4catia se cuelgue ni
  lance un error — solo deja de surtir efecto en CATIA.

Esto confirma que el *guard* de foco (`should_inject`) está detectando y
omitiendo la inyección correctamente en vez de fallar de forma confusa.

## 10. Cambio automático de perfil con debounce

Cambia de workbench en CATIA repetidamente, incluyendo cambios rápidos de
ida y vuelta. Confirma que el perfil activo solo cambia tras varias lecturas
consecutivas estables de `GetWorkbenchId()` (el valor por defecto de
`AppConfig.debounce_count`), no en cada parpadeo transitorio de la UI.

## 11. La captura de un binding no dispara también el antiguo

Si usas (o integras) el flujo de captura de `ds4catia/gui/capture.py`: entra
en modo "escuchando" para un control que ya tiene un binding activo en
CATIA, pulsa ese control, y confirma que la acción **antigua** no se
dispara también mientras se está capturando la nueva asignación.

## 12. Cerrar la ventana deja el motor corriendo en bandeja

Cierra la ventana principal con la X. Confirma que el proceso sigue
corriendo (visible en el icono de bandeja) y que el mando sigue despachando
acciones hacia CATIA. Solo "Salir" desde la bandeja debe terminar el
proceso.

## 13. Si el antivirus/EDR marca la aplicación

Esto es lo esperado en un primer despliegue (ver
[`troubleshooting.md`](troubleshooting.md)), no un fallo a "arreglar" en el
código. Sigue el procedimiento de esa página: pide a tu equipo de seguridad
que permita (*allowlist*) el hash/binario concreto, en vez de asumir que
firmar el ejecutable basta por sí solo.
