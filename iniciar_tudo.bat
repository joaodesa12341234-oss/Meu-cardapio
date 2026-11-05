@echo off
rem Liga a ponte do WhatsApp e o cardapio juntos. Este e o arquivo que inicia com o Windows.
cd /d "%~dp0"
if exist "%~dp0ponte_whatsapp\ponte.js" start "Ponte WhatsApp" /min cmd /c "%~dp0iniciar_ponte_whatsapp.bat"
timeout /t 3 /nobreak >nul
call "%~dp0iniciar_cardapio.bat"
