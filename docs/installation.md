# Instalación

> **Estado del proyecto**: ds4catia se desarrolló íntegramente en un entorno
> Linux sin Windows, sin CATIA V5 real y sin un mando DualShock 4 físico. Todo
> el código específico de Windows (lectura HID real, automatización COM,
> `SendInput`, comportamiento visual de la GUI) está escrito y probado con
> *fakes*/mocks, pero **nunca se ha ejecutado contra hardware o CATIA
> reales**. Antes de depender de esto en producción, sigue
> [`manual_verification_checklist.md`](manual_verification_checklist.md) en
> tu propia máquina Windows.

## Requisitos

- **Windows 10/11** (CATIA V5 y la automatización COM son específicos de
  Windows; en Linux/macOS solo puedes ejecutar la suite de tests y el modo
  `--dry-run` sin mando/CATIA reales).
- **Python 3.10 o superior**.
- **CATIA V5** instalado y, para usar la app de verdad, ya abierto (ds4catia
  se conecta a una instancia de CATIA en ejecución vía COM; no la lanza él).
- Un mando **DualShock 4**, conectado preferentemente por **USB** (ver
  [`troubleshooting.md`](troubleshooting.md) sobre por qué USB es la conexión
  recomendada por defecto y Bluetooth queda como secundaria).
- No se necesita instalar ningún **driver** adicional (no hay HidHide, no
  hay ViGEmBus): ds4catia lee el mando directamente como dispositivo HID
  genérico en modo usuario, sin privilegios de administrador. Sí hace falta
  copiar un archivo `hidapi.dll` (no es un driver, solo una librería) — ver
  el paso dedicado más abajo.

## Instalar desde el código fuente

```bash
git clone <url-del-repo>
cd Dualshock4CatiaV5
pip install -e .[dev]
```

Esto instala las dependencias del proyecto (`pydantic`, `jsonschema`,
`PySide6`, y en Windows además `hidapi`, `pywin32`, `pycatia`) más las de
desarrollo (`pytest`, `pytest-qt`, `ruff`).

Para comprobar que la instalación funciona, corre la suite de tests (no
necesita Windows ni hardware):

```bash
pytest -q
```

### Paso adicional obligatorio en Windows: `hidapi.dll`

El paquete `hidapi` de Python es solo un envoltorio — **no incluye el
`hidapi.dll` real**, así que hay que ponerlo a mano una sola vez:

1. Descarga el zip de Windows desde
   <https://github.com/libusb/hidapi/releases/latest> (el archivo termina
   en `-win.zip`).
2. Descomprímelo y coge `x64\hidapi.dll` (Python de 64 bits, lo habitual).
3. Copia ese `hidapi.dll` a la raíz del proyecto, junto a `pyproject.toml`
   (la carpeta desde la que ejecutas los comandos de `ds4catia`).

Confirma que funcionó con:

```bash
python -c "import hid; print(hid.__file__)"
```

Si da error, consulta [`troubleshooting.md`](troubleshooting.md).

## Ejecutar la aplicación

### Forma rápida: scripts `.bat`

Una vez instalado (pasos de arriba ya hechos, con `.venv` creado), puedes
simplemente hacer **doble clic** en uno de estos archivos en la raíz del
proyecto, sin activar nada a mano ni abrir una consola tú mismo:

- `run.bat` — GUI normal (acepta los mismos flags que `ds4catia`, p. ej.
  arrastrando/editando el acceso directo para añadir `--connection usb`).
- `run-dry.bat` — GUI con CATIA simulado (`--dry-run`): no toca CATIA
  real, pero sí lee el mando real si está conectado.
- `run-diagnose.bat` — sin GUI, contra CATIA real, imprimiendo en
  consola cada acción que se dispara al tocar el mando
  (`--headless --verbose`). El modo más útil mientras se depuran fallos.

Si la ventana se cierra sola al dar error, usa `run-diagnose.bat` (deja
la consola abierta) en vez de `run.bat` para ver qué pasó.

### Forma manual (equivalente)

Con la GUI (por defecto):

```bash
ds4catia
```

o, equivalentemente:

```bash
python -m ds4catia.main
```

Sin GUI, como proceso en segundo plano:

```bash
ds4catia --headless
```

En modo de prueba sin CATIA ni mando real (usa un backend de CATIA simulado
y no intenta leer hardware real; útil para explorar la app o para desarrollo
en Linux/macOS):

```bash
ds4catia --dry-run
```

Otros flags útiles (ver `ds4catia --help` para la lista completa):

| Flag | Qué hace |
|---|---|
| `--profile DIR` | Usa un directorio de perfiles `*.json` distinto al bundleado por defecto (`ds4catia/mapping/profiles/`). |
| `--default-profile ID` | Perfil activo al iniciar (por defecto `base`). |
| `--connection {usb,bt,auto}` | Modo de conexión del mando. |
| `--list-devices` | Lista los mandos DS4 detectados por HID y sale. |
| `--dry-run` | Backend de CATIA simulado, sin inyección de input real. |
| `--no-gui` / `--headless` | Corre el motor en segundo plano sin ventana ni bandeja. |

## Compilar un `.exe` independiente (opcional)

El repo incluye `ds4catia.spec` para [PyInstaller](https://pyinstaller.org/),
pero **compilarlo es responsabilidad del usuario en su propia máquina
Windows** — este proyecto se construyó en un contenedor Linux que no puede
generar ni probar un ejecutable de Windows. En Windows, con las dependencias
instaladas:

```powershell
pip install .[dev] pyinstaller
pyinstaller ds4catia.spec
```

Esto produce `dist/ds4catia/ds4catia.exe`. Pruébalo manualmente antes de
distribuirlo — ver también la nota sobre antivirus/EDR en
[`troubleshooting.md`](troubleshooting.md).

## Siguiente paso

Sigue con [`profiles_and_layers.md`](profiles_and_layers.md) para entender
cómo se configuran los mapeos, o con
[`remapping_gui.md`](remapping_gui.md) para aprender a usar la GUI.
