@echo off
rem Instalacao automatica: roda UMA vez no computador da lanchonete.
cd /d "%~dp0"
echo === Instalando o Cardapio da Lanchonete ===
where python >nul 2>nul || (echo Falta o Python. Instale em python.org marcando "Add Python to PATH" e rode de novo. & pause & exit /b 1)
where node >nul 2>nul || (echo Falta o Node.js. Instale o LTS em nodejs.org e rode de novo. & pause & exit /b 1)
echo [1/5] Pacotes do cardapio...
python -m pip install -q -r requirements.txt
echo [2/5] Configuracao e chaves...
python configurar.py
echo [3/5] Pacotes da ponte do WhatsApp...
pushd ponte_whatsapp
call npm install --omit=dev
popd
echo [4/5] Iniciar junto com o Windows e atalho do painel...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Cardapio da Lanchonete.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0iniciar_oculto.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
call criar_atalho_painel.bat
echo [5/5] Impedindo que o computador entre em suspensao na tomada...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo.
echo Pronto! Agora ligando tudo. Na janela "Ponte WhatsApp" aparece um QR code:
echo leia com o WhatsApp do chip da lanchonete (Aparelhos conectados).
echo Depois disso nao precisa mais fazer nada: tudo liga sozinho com o Windows.
pause
rem primeira vez com janelas visiveis para ler o QR code
start "" "%~dp0iniciar_tudo.bat"
