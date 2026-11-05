@echo off
rem Liga o cardapio e religa sozinho se ele cair. Coloque um atalho deste arquivo na pasta
rem "shell:startup" (ou use o Agendador de Tarefas, veja COLOCAR_NO_AR.md) para iniciar com o Windows.
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
rem instala/atualiza os pacotes necessarios (so demora na primeira vez)
python -m pip install -q --disable-pip-version-check -r requirements.txt 2>nul
:ligar
echo [%date% %time%] Iniciando o cardapio...
python servir.py
echo [%date% %time%] O servidor parou. Religando em 5 segundos... (feche esta janela para sair)
ping -n 6 127.0.0.1 >nul
goto ligar
