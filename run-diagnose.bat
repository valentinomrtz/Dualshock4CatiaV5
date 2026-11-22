@echo off
rem Sin GUI, contra CATIA real, imprimiendo en consola cada accion que
rem se dispara al tocar el mando. El modo que hemos estado usando para
rem diagnosticar fallos paso a paso. Cierra con Ctrl+C.
call "%~dp0run.bat" --headless --verbose
