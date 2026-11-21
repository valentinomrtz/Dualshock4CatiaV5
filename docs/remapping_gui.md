# Usar la GUI de configuración

Al ejecutar `ds4catia` (sin `--headless`), se abre la ventana principal del
editor de perfiles junto con un icono en la bandeja del sistema. El motor de
mapeo sigue corriendo en segundo plano aunque cierres la ventana — solo se
detiene al elegir "Salir" desde el icono de bandeja.

## Ventana principal

- **Lista de perfiles** (izquierda): todos los perfiles cargados desde el
  directorio de perfiles (por defecto `ds4catia/mapping/profiles/`, o el que
  hayas indicado con `--profile DIR`). Selecciona uno para ver/editar sus
  bindings.
- **Árbol de bindings** (pestaña "Bindings y capas"): lista cada binding del
  perfil seleccionado, agrupado en "bindings base" y un grupo por cada
  `Layer`. Doble clic (o el botón "Editar binding") abre un diálogo para
  cambiar su control de entrada, el tipo de acción destino (las 9 variantes
  descritas en [`profiles_and_layers.md`](profiles_and_layers.md)) y sus
  modificadores analógicos/digitales.
- **Gestión de capas**: botones para añadir/editar/eliminar una `Layer`
  (control disparador, modo *hold*/*toggle*) y los bindings dentro de sus
  `overrides`.
- **Importar / Exportar**: cargan o guardan un perfil como archivo `*.json`.
  Un JSON inválido muestra un mensaje de error legible en vez de romper la
  aplicación (y no modifica el perfil que ya tenías cargado).
- **Guardar**: escribe el perfil editado de vuelta a su archivo de origen.
  Un indicador de estado muestra si hay cambios sin guardar.

> ⚠️ **Los cambios requieren reiniciar la app para tener efecto en el motor
> en ejecución.** El editor carga su propia copia en memoria de los
> perfiles, independiente de los que ya cargó el motor (`Ds4CatiaDaemon`)
> al arrancar. Guardar un perfil edita correctamente el archivo `.json` en
> disco, pero el motor que ya está corriendo seguirá usando los bindings con
> los que arrancó hasta que reinicies `ds4catia`. No hay recarga en caliente
> en esta versión.

## Pestaña "Captura en vivo / Dry-run"

Panel de prueba que te deja ejercitar los bindings **sin necesidad de un
mando DS4 real conectado**: botones para simular una pulsación de
cross/circle/square/triangle, y deslizadores para simular el stick
izquierdo. Cada acción resultante se muestra en un registro junto con el
resultado del despacho (si se envió correctamente a CATIA/inyección, o se
omitió y por qué). Si el backend de CATIA activo es el simulado
(`--dry-run`), también verás el registro exacto de llamadas que se le
habrían hecho a CATIA real.

Este panel es útil para verificar la lógica de tus mapeos antes de probarlos
contra CATIA de verdad, y también como ayuda de diagnóstico si algo no se
dispara como esperas.

## Icono de bandeja del sistema

El menú de la bandeja ofrece:

- **Pausar / Reanudar** — detiene o reanuda el despacho de acciones (el
  motor sigue leyendo el mando, pero no envía nada a CATIA/inyección
  mientras está en pausa).
- **Perfil activo: `<id>`** — indicador de solo lectura del perfil
  actualmente activo (el cambio de perfil, manual o automático por
  workbench, se controla desde los bindings `app_control` del perfil o por
  auto-detección — no desde este menú).
- **Abrir configuración…** — muestra/trae al frente la ventana principal
  (puedes cerrarla con la X sin que la app deje de funcionar en segundo
  plano).
- **Salir** — detiene el motor de mapeo de forma ordenada y cierra la
  aplicación.

## Captura de un control (construida pero no integrada aún en el editor)

El proyecto incluye un flujo de "captura y confirmación" (`CaptureController`/
`CaptureDialog` en `ds4catia/gui/capture.py`) pensado para, en una futura
iteración, dejar pulsar físicamente el control del mando que quieras asignar
en vez de escribir su nombre a mano en el editor de bindings. Esta pieza ya
está implementada y probada de forma aislada (con bindings existentes
suprimidos correctamente mientras se captura, para no disparar la acción
antigua), pero en esta versión **no está conectada todavía** al diálogo de
edición de bindings de la ventana principal — editar un binding hoy requiere
escribir el nombre del control manualmente. Si quieres usarla, puedes
invocarla directamente desde código (`CaptureDialog(engine)`); conectarla al
editor es un trabajo de UI pendiente, no un problema de la lógica subyacente.
