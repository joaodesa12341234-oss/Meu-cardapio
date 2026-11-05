@echo off
cd /d "%~dp0ponte_whatsapp"
where node >nul 2>nul || (echo Instale o Node.js LTS em https://nodejs.org e tente de novo. & pause & exit /b 1)
if not exist node_modules (echo Instalando dependencias, aguarde... & call npm install --omit=dev)
:loop
node ponte.js
if errorlevel 2 (
  if not defined PONTE_OCULTO pause
  exit /b 2
)
echo Ponte parou. Reiniciando em 5s... (feche a janela para sair)
ping -n 6 127.0.0.1 >nul
goto loop
