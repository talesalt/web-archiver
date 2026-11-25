@echo off
rem Abre um Edge separado (perfil dedicado) com porta de debug local.
rem Uso: launch_edge.bat [URL] [PORTA]
setlocal
set "EDGE_EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_EXE%" set "EDGE_EXE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
set "PROFILE_DIR=%LOCALAPPDATA%\web-archiver\edge-profile"
set "DEBUG_PORT=%~2"
if "%DEBUG_PORT%"=="" set "DEBUG_PORT=9222"
set "START_URL=%~1"
if "%START_URL%"=="" set "START_URL=about:blank"

start "" "%EDGE_EXE%" ^
  --remote-debugging-port=%DEBUG_PORT% ^
  --remote-debugging-address=127.0.0.1 ^
  --user-data-dir="%PROFILE_DIR%" ^
  --profile-directory=Default ^
  --no-first-run ^
  --no-default-browser-check ^
  --disable-features=msEdgeSidebarV2 ^
  "%START_URL%"

ping -n 4 127.0.0.1 >nul
curl.exe -s http://127.0.0.1:%DEBUG_PORT%/json/version
echo.
echo Pronto. Se o site pedir login, aceite de termos ou verificacao, conclua manualmente na janela do Edge.
endlocal
