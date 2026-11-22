@echo off
rem GUI con CATIA simulado (FakeCatiaBackend): no toca CATIA real, pero
rem SI lee el mando real si esta conectado. Util para probar mapeos sin
rem riesgo de que una accion equivocada haga algo no deseado en CATIA.
call "%~dp0run.bat" --dry-run
