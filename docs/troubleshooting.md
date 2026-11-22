# Solución de problemas y riesgos conocidos

Esta página recoge los riesgos y limitaciones **ya identificados por la
investigación previa del proyecto**
([`reports/Mando DualShock4 para CATIA V5.md`](../reports/Mando%20DualShock4%20para%20CATIA%20V5.md)
y las notas en
[`research_notes/`](../research_notes/Mando%20DualShock4%20para%20CATIA%20V5/)),
no solo problemas hipotéticos. Como el proyecto se construyó sin acceso a
Windows/CATIA/mando reales, varios de estos puntos todavía no se han podido
confirmar en la práctica — se indica explícitamente dónde es así.

## Windows navega solo por el menú Inicio / la UI mientras el mando está por Bluetooth

**Confirmado durante la verificación manual**: con el DS4 emparejado por
Bluetooth, al abrir el menú Inicio de Windows (o cualquier UI del sistema
navegable con mando), la selección se movía sola, como si una flecha
estuviera mantenida pulsada — sin que `ds4catia` estuviera haciendo nada con
ese control. Al desconectar el mando por Bluetooth, el problema desapareció
de inmediato.

Esto **no es un fallo de ds4catia**: Windows 11 tiene soporte nativo de
navegación por mando en el menú Inicio y otras partes del shell, y como el
proyecto deliberadamente no usa ningún driver que "oculte" el mando en
exclusiva (ver más abajo, "no se necesita ningún driver adicional"), Windows
puede leer el stick directamente por su cuenta al mismo tiempo que nuestra
app, interpretando el drift/inclinación del stick como una navegación de
UI. Es la contrapartida esperada de esa decisión de diseño.

**Qué hacer:** usa **USB** como conexión principal (ya es la recomendada por
defecto en este proyecto) — por USB no se ha observado este comportamiento.
Si necesitas Bluetooth sí o sí y te molesta esta interferencia, revisa en
Windows: Configuración → Bluetooth y dispositivos → Mandos de juego /
Accesibilidad → si hay una opción para desactivar la navegación del shell
por mando (varía según la versión de Windows 11).

## `ImportError: Unable to load any of the following libraries: ... hidapi.dll ...`

**Confirmado durante la verificación manual en una máquina Windows real.**
Si ves este error al ejecutar `ds4catia` o `python -m ds4catia.main`, falta
un archivo `hidapi.dll` en algún lugar donde Windows pueda encontrarlo.

Hay dos paquetes de PyPI distintos que ambos se importan como `hid` en
Python (`hid`, de apmorton, y `hidapi`, mantenido por Trezor/cython-hidapi
— este proyecto usa este segundo). **Ninguno de los dos trae el `.dll` de
hidapi dentro del paquete para Windows** — ambos son solo el envoltorio de
Python y esperan que `hidapi.dll` ya esté presente en el sistema por
separado. (Esto corrige una afirmación anterior de esta misma página: se
asumió que el paquete `hidapi` sí lo incluía, y no es así — confirmado
inspeccionando el contenido real del wheel de Windows.)

**Solución** — descargar el `.dll` oficial y ponerlo donde Windows lo
encuentre:

1. Ve a <https://github.com/libusb/hidapi/releases/latest> y descarga el
   zip de Windows (termina en `-win.zip`).
2. Descomprímelo y entra en la carpeta `x64` (asumiendo Python de 64 bits,
   lo habitual).
3. Copia `hidapi.dll` a la carpeta raíz del proyecto (la misma desde la
   que ejecutas `python -m ds4catia.main` — Windows también busca DLLs en
   el directorio de trabajo actual, así que no hace falta tocar el PATH
   del sistema ni copiarlo a `System32`).
4. Confirma con `python -c "import hid; print(hid.__file__)"` que ya no da
   error antes de volver a intentar `ds4catia --list-devices`.

Si, además de este error, confirmas que tienes instalado el paquete
equivocado (`hid` en vez de `hidapi`), corrígelo con:

```bash
pip uninstall hid -y
pip install hidapi
```

## `pip install hidapi` falla pidiendo "Microsoft Visual C++ 14.0 or greater"

**Confirmado durante la verificación manual**, en una máquina con una versión
de Python muy reciente (p. ej. 3.15) recién salida. `hidapi` es una
extensión compilada en C; PyPI solo ofrece paquetes ya compilados
("wheels") para las versiones de Python que llevan ya un tiempo publicadas.
Si usas una versión de Python demasiado nueva, puede que `hidapi` (u otra
dependencia) aún no tenga wheel para ella, y pip intenta compilarla desde
el código fuente — lo cual requiere tener instalado el compilador de
Microsoft C++ ("Build Tools"), que no suele venir instalado por defecto.

La forma más rápida de evitar esto, sin instalar varios GB de herramientas
de compilación, es usar una versión de Python algo más asentada (3.11, 3.12
o 3.13) solo para este proyecto, en un entorno virtual aislado de tu Python
del sistema:

```bash
py list
py install 3.12
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

(`py list`/`py install` requieren el "Python Install Manager" de
python.org; si no lo tienes, descarga el instalador de Python 3.12 desde
python.org directamente). A partir de ahí, usa siempre `python`/`pip`
**dentro de ese entorno virtual activado** para este proyecto.

## El antivirus/EDR corporativo marca la aplicación

**Esto es lo esperado en un primer despliegue, no un fallo del software.**
ds4catia combina tres capacidades que, juntas, coinciden con el perfil de
comportamiento que varios EDR de nivel empresarial ya detectan activamente
como sospechoso de keylogger: lectura HID de bajo nivel, inyección de input
sintético (`SendInput`) y automatización COM de una aplicación de terceros.
La investigación documenta un caso concreto: una actualización de reglas de
Carbon Black de junio de 2025 añadió detecciones contra el patrón
`SetWindowsHookEx`, y Cortex XDR de Palo Alto tiene una alerta nombrada
explícitamente para este tipo de herramientas (su propia guía de
investigación menciona AutoHotkey como ejemplo habitual).

**Qué hacer:**
1. No asumas que firmar el ejecutable (code signing) evita esta detección —
   ayuda a la reputación estática (SmartScreen, listas de confianza por
   publisher) pero ninguna fuente confirma que evite las detecciones de
   comportamiento.
2. Antes de desplegar en un entorno corporativo, coordina con el equipo de
   seguridad: pide que se permita (*allowlist*) el hash/binario concreto,
   igual que recomienda el propio sitio oficial de AutoHotkey para sus
   falsos positivos, en vez de desplegar y esperar a que falle.

## Entornos VM/VDI/Citrix

**No hay solución conocida ni documentación autorizada** sobre cómo se
comporta la API COM de CATIA V5, ni la inyección de input, dentro de una
sesión de máquina virtual o VDI/Citrix. Lo que sí está documentado y es
sugestivo:

- El licenciamiento de CATIA puede restringir explícitamente la
  virtualización para ciertos tipos de licencia.
- Citrix bloquea por defecto los canales virtuales personalizados desde
  Virtual Apps and Desktops 7 2109.
- Un partner de Dassault no recomienda Remote Desktop para trabajo de
  producción con CATIA.
- La redirección USB genérica de un mando a una sesión VDI es, según la
  propia documentación de Citrix, propensa a fallos con "algunos
  dispositivos USB".

**Qué hacer:** si tu CATIA corre en una VM/VDI, prueba primero con una
prueba de concepto mínima (conexión COM + una acción simple) en ese entorno
exacto antes de invertir tiempo configurando perfiles completos. No hay
checklist que pueda sustituir a esa prueba.

## UIPI bloquea `SendInput` en silencio

`SendInput` solo funciona sobre la ventana que tiene el foco real del
sistema, y está sujeta a *User Interface Privilege Isolation* (UIPI): si
CATIA se ejecuta con un nivel de integridad distinto al de ds4catia (por
ejemplo, CATIA lanzado "Ejecutar como administrador" mientras ds4catia corre
como usuario normal), la inyección de input **se bloquea sin ningún error
visible** — `GetLastError` no indica el motivo.

**Síntoma:** los bindings de tipo `catia_command`/`catia_camera`/
`catia_selection` (que usan la API COM) siguen funcionando, pero los
bindings `keyboard`/`mouse_move`/`mouse_button` (que usan `SendInput`) no
hacen nada, sin mensaje de error.

**Qué hacer:** ejecuta CATIA y ds4catia con el mismo nivel de privilegio
(ambos como usuario normal, sin "Ejecutar como administrador" en ninguno de
los dos). El paso 9 de
[`manual_verification_checklist.md`](manual_verification_checklist.md)
describe cómo confirmar este comportamiento deliberadamente.

## El mando por Bluetooth no reporta touchpad/giroscopio

El DS4 arranca por Bluetooth en un modo "básico" sin touchpad ni
giroscopio. A diferencia del driver de Linux, Windows no activa
automáticamente el modo extendido — ds4catia hace ese *handshake* él mismo
al conectar (`DS4Device.perform_bt_extended_handshake()`), pero si algo
falla en ese paso te quedarás sin esos datos.

**Qué hacer:** si el touchpad/giroscopio no responden, conecta el mando por
**USB primero** (es la conexión recomendada por defecto, no necesita ningún
*handshake*) para aislar si el problema es específico de Bluetooth antes de
investigar más.

## Los IDs de workbench no coinciden / el cambio de perfil no funciona

Solo el ID de Part Design (`"PrtCfg"`) está confirmado por la investigación
del proyecto; los de Assembly Design, Sketcher, Drafting y Generative Shape
Design en los perfiles por defecto son **estimaciones sin verificar**. Si el
cambio automático de perfil al cambiar de workbench no funciona, es
probable que el `auto_activate.match` del perfil correspondiente no
coincida con lo que tu CATIA real devuelve.

**Qué hacer:** sigue el procedimiento en
[`ds4catia/mapping/profiles/README.md`](../ds4catia/mapping/profiles/README.md)
para capturar el `GetWorkbenchId()` real de cada workbench y corregir el
JSON correspondiente.

## Los `command_id` de un binding `catia_command` no hacen nada

Los nombres de comando usados en los perfiles por defecto (p. ej.
`"PartDesign.Pad"`) son plausibles pero no verificados contra una
instalación real. Ábrelos desde CATIA en **View > Command List…** (o
**Tools > Customize > Commands**) para confirmar/corregir el `command_id`
exacto en el perfil.

## No se necesita ningún driver adicional — y es intencional

Si vienes de herramientas como DS4Windows, puede sorprenderte que ds4catia
no instale HidHide ni un mando virtual (ViGEmBus). Es deliberado: ambos
proyectos están archivados/sin mantenimiento upstream, y como CATIA no
tiene ningún soporte nativo de gamepad con el que el DS4 "crudo" pudiera
entrar en conflicto, no hay problema de doble-input que resolver. Esto
también significa que ds4catia no necesita privilegios de administrador
para funcionar.
