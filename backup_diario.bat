@echo off
rem Backup diario do cardapio (banco + fotos). Agende com:
rem   schtasks /Create /SC DAILY /ST 04:00 /TN "Backup Cardapio" /TR "\"%~f0\""
rem Troque a linha cd abaixo se a pasta do projeto for outra (por padrao usa a pasta deste arquivo).
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python manage.py backup --manter 14 >> dados\backup.log 2>&1
