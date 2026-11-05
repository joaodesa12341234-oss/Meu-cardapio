@echo off
rem Cria na area de trabalho o atalho "Painel da Lanchonete" (com o icone do hamburguer).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Painel da Lanchonete.lnk'); $s.TargetPath = '%~dp0abrir_painel.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0app.ico'; $s.WindowStyle = 7; $s.Description = 'Abre o painel da lanchonete'; $s.Save()"
if errorlevel 1 (
  echo Nao foi possivel criar o atalho. Clique com o botao direito em abrir_painel.bat e escolha Enviar para - Area de trabalho.
) else (
  echo Pronto! O atalho "Painel da Lanchonete" esta na sua area de trabalho.
)
pause
