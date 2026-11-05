@echo off
rem Abre o PAINEL da lanchonete como se fosse um aplicativo (janela propria, sem barra de enderecos).
rem Se o cardapio ainda nao estiver ligado, liga sozinho. Para criar o atalho na area de trabalho,
rem de dois cliques em criar_atalho_painel.bat.
cd /d "%~dp0"
set PORTA=8000
set URL=http://localhost:%PORTA%/admin

call :no_ar
if not errorlevel 1 goto abrir

echo Ligando o cardapio, aguarde...
start "" wscript.exe "%~dp0iniciar_oculto.vbs"
for /l %%i in (1,1,60) do (
  call :no_ar
  if not errorlevel 1 goto abrir
  ping -n 2 127.0.0.1 >nul
)
echo Nao foi possivel ligar o cardapio. Abra o iniciar_cardapio.bat e veja a mensagem de erro.
pause
exit /b 1

:abrir
start "" msedge --app=%URL% 2>nul
if not errorlevel 1 exit /b 0
start "" chrome --app=%URL% 2>nul
if not errorlevel 1 exit /b 0
start "" %URL%
exit /b 0

:no_ar
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%PORTA%/saude; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
exit /b %errorlevel%
