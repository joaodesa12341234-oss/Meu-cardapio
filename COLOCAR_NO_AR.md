# Roteiro: colocar a lanchonete no ar (Windows)

Siga na ordem. Cada passo tem um "✔ Conferiu?" para você ter certeza antes de seguir.
Os comandos são do **PowerShell**, abertos **na pasta do projeto** (clique com o botão direito na pasta → "Abrir no Terminal").

---

## 1. Segurança primeiro (10 minutos)

**1.1 Troque a chave da Evolution.** A chave antiga (`joao88550206`) foi exposta e precisa ser descartada.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Rode 4 vezes e use cada resultado em um destes campos do arquivo `.env` (copie de `.env.example` se ainda não existir):

```
EVOLUTION_API_KEY=<primeiro valor>
REDIS_PASSWORD=<segundo valor>
POSTGRES_PASSWORD=<terceiro valor>
SECRET_KEY=<quarto valor>
```
Não mande esses valores para ninguém, nem por chat. Na primeira vez que fizer isso com a Evolution já em uso,
recrie os contêineres para ela assumir a chave nova: `docker compose up -d --force-recreate`.
(Se mudar `POSTGRES_PASSWORD` com banco já criado, o Postgres não aceita a senha nova sozinho — nesse caso é mais
simples recomeçar a Evolution: `docker compose down -v`, `docker compose up -d` e ler o QR do WhatsApp de novo.)

**1.2 Endereço público.** Quando você já souber o endereço do site, acrescente ao `.env`:
```
PUBLIC_URL=https://seu-endereco.com.br
```
Ele é usado no link de acompanhamento enviado por WhatsApp e no cartaz com QR code.

**1.3 Senhas do painel.**
```powershell
python manage.py trocar-senha admin
python manage.py criar-usuario cozinha --papel cozinha
python manage.py listar
```
Use o usuário `cozinha` no tablet: ele não vê financeiro nem configurações.

✔ Conferiu? O arquivo `.env` existe, tem os 4 valores e **não** está em nenhum e-mail, print ou grupo.

---

## 2. Instalar e ligar

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python servir.py
```
Abra `http://localhost:8000` (cardápio) e `http://localhost:8000/admin` (painel).
O `.env` é lido automaticamente; você não precisa digitar `$env:...`.

✔ Conferiu? O cardápio abre, o login do painel funciona e, no topo do painel, o selo do WhatsApp aparece
(vermelho enquanto você não conectar o WhatsApp no passo 3).

---

## 0. Instalação automática (recomendado)

No computador da lanchonete, instale o **Python** (marcando "Add Python to PATH") e o **Node.js LTS** e dê dois cliques em
`instalar_no_windows.bat`. Ele instala os pacotes, cria o `.env` com chaves aleatórias, faz tudo ligar junto com o Windows
(`iniciar_tudo.bat`), cria o atalho do painel na área de trabalho e desliga a suspensão. Só falta ler o QR code do WhatsApp
uma vez. Depois disso, ligou o computador, está funcionando.

**Sem janelas pretas:** depois que o WhatsApp estiver conectado, o início com o Windows já usa `iniciar_oculto.vbs`, que liga tudo em
segundo plano (os textos ficam em `ponte.log` e `cardapio.log`). Para parar tudo, use `parar_tudo.bat`. Se um dia precisar ler o QR
code de novo, rode `parar_tudo.bat` e abra `iniciar_tudo.bat` (com janelas).

## 3. Conectar o WhatsApp (ponte leve, sem Docker)

A pasta `ponte_whatsapp` faz o papel da Evolution API sem precisar de Docker nem de virtualização.

1. Instale o **Node.js LTS** (https://nodejs.org) e reinicie o cmd.
2. No `.env`, defina `EVOLUTION_API_KEY=` com uma senha longa inventada por você (não cole em conversas) e salve.
3. Dê dois cliques em `iniciar_ponte_whatsapp.bat`. Na primeira vez ele roda `npm install` (precisa de internet).
4. Aparece um **QR code** na janela preta. No celular do chip de teste: WhatsApp → Aparelhos conectados → Conectar um aparelho → leia o QR.
   (Alternativa sem QR: coloque `PONTE_PAREAR_NUMERO=5511999998888` no `.env` e use "Conectar com número de telefone".)
5. Deve aparecer "✔ WhatsApp conectado". **Reinicie o cardápio**. O selo do painel fica **🟢 WhatsApp conectado**.
6. Na aba **Loja → Funcionamento e entrega**, preencha o **WhatsApp do dono**.

Deixe a janela da ponte aberta. A sessão fica salva em `ponte_whatsapputh` (é uma credencial: não compartilhe). Se o celular
desconectar o aparelho, apague essa pasta e abra de novo.
Use um número só da loja/teste: é tecnologia não oficial (como a Evolution) e há risco de bloqueio.

*(Se um dia puder usar Docker, `docker compose up -d` com a Evolution continua funcionando; o cardápio não muda.)*

---

## 4. Deixar ligado sozinho

O arquivo `iniciar_cardapio.bat` liga o cardápio e religa se ele cair. Para iniciar junto com o Windows:

1. Aperte `Win + R`, digite `shell:startup`, Enter.
2. Arraste para essa pasta um **atalho** do `iniciar_cardapio.bat` (botão direito → Criar atalho).

No computador da loja, desative a suspensão automática (Configurações → Sistema → Energia → "Nunca" ao ligar na tomada).
Coloque também um atalho do `iniciar_ponte_whatsapp.bat` na pasta de inicialização.

**Atalho do painel na área de trabalho:** dê dois cliques em `criar_atalho_painel.bat`. Ele cria o atalho "Painel da Lanchonete", que abre o painel como um aplicativo (e liga o cardápio, se estiver desligado).

✔ Conferiu? Reinicie o computador e veja se o cardápio volta sozinho.

---

## 5. Acesso pela internet (HTTPS)

Escolha **uma** das opções:

**A) Hospedagem em servidor (VPS) com Caddy — a mais robusta.**
Funciona 24 h sem depender do computador da loja. O Caddy obtém e renova o HTTPS sozinho.
No servidor Linux use `gunicorn wsgi:app -c gunicorn.conf.py` (veja "Colocar na internet" no `README.md`) e, no `.env`:
```
TRUST_PROXY=1
COOKIE_SECURE=1
```

**B) Cloudflare Tunnel a partir do computador da loja — sem abrir portas no roteador.**
Exige um domínio seu gerenciado na Cloudflare. Crie um túnel nomeado apontando para `http://localhost:8000`
(passo a passo no painel Cloudflare Zero Trust → Networks → Tunnels). No `.env`:
```
TRUST_PROXY=1
COOKIE_SECURE=1
```
(O "quick tunnel" sem domínio gera um endereço que muda toda vez — serve só para testar, não para o QR impresso.)

**C) Só dentro do Wi-Fi da loja (sem internet).** Dê um IP fixo ao computador, divulgue `http://IP:8000` e libere a porta
8000 no Firewall do Windows apenas para a rede privada. Não use para pedidos de fora.

Depois de ter o endereço final: coloque em `PUBLIC_URL`, reinicie o servidor e **imprima o cartaz** em
Loja → "Abrir cartaz para imprimir".

✔ Conferiu? Do 4G do seu celular (Wi-Fi desligado), o endereço abre o cardápio com o cadeado de HTTPS.

---

## 6. Backup (não pule)

```powershell
schtasks /Create /SC DAILY /ST 04:00 /TN "Backup Cardapio" /TR "\"C:\caminho\do\projeto\backup_diario.bat\""
```
(troque o caminho). Os backups ficam em `dados\backups`. **Copie essa pasta de vez em quando para um pendrive ou
nuvem**: backup só no mesmo computador não protege contra pane ou roubo.

✔ Conferiu? Rode `python manage.py backup` uma vez e veja o arquivo novo em `dados\backups`.

---

## 7. Pedido de teste de ponta a ponta (faça antes de divulgar)

1. No celular, abra o cardápio pelo endereço público e faça um pedido de **entrega** com adicionais, para o seu próprio número.
2. Você deve receber no WhatsApp: a confirmação com o **link de acompanhamento** (o dono também recebe o aviso de pedido novo).
3. No painel: o pedido aparece com som; abra a **Comanda** e confira a impressão.
4. Na **Tela da cozinha** (`/admin/cozinha`): clique em "Começar" e "Pronto"; o link de acompanhamento deve acompanhar.
5. Mude para "Saiu" e "Entregue"; confira as mensagens de WhatsApp.
6. Em **Financeiro**, o pedido aparece em "Entregas por bairro" e no faturamento.
7. Cancele um pedido de teste e confirme que ele sai do faturamento.
8. Apague os pedidos de teste da sua cabeça: eles ficam no financeiro. Se quiser zerar antes de abrir, faça um backup e comece com um banco novo (apague `dados\cardapio.db` com o servidor parado — o cardápio inicial é recriado).

---

## 8. Rotina

- **Todo dia:** ao abrir, confira o selo 🟢 do WhatsApp e se o "Funcionamento" está certo.
- **Toda semana:** copie `dados\backups` para fora do computador; olhe o Financeiro.
- **Todo mês:** `python manage.py anonimizar --dias 180` (LGPD) e atualize o sistema (`pip install -r requirements.txt`).

## 9. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Selo "ponte não está respondendo" | Ponte desligada | Abra `iniciar_ponte_whatsapp.bat` |
| "Evolution recusou a chave" | `EVOLUTION_API_KEY` diferente da da ponte | Use a mesma nos dois (arquivo `.env`) e reinicie ambos |
| "WhatsApp desconectado" | Celular perdeu a sessão | Leia o QR code de novo na Evolution |
| Cliente não recebe mensagem | Selo vermelho ou número sem WhatsApp | Resolva o selo; confira o DDD do cliente |
| Não consigo entrar no painel (muitas tentativas) | Bloqueio de 15 min | `python manage.py desbloquear` |
| Site fora do ar após reiniciar | Atalho de inicialização faltando | Passo 4 |
