@echo off
rem Fecha o cardapio e a ponte que estao rodando (inclusive os ocultos).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'servir\.py|ponte\.js|iniciar_cardapio|iniciar_ponte' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Tudo parado.
pause
