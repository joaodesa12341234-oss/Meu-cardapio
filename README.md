# Cardápio Digital — Lanchonete (Python + Flask + SQLite)

Cardápio de autoatendimento feito para o **celular do cliente**: abas de categorias,
lista de produtos com foto, carrinho, retirada no balcão ou entrega, e senha do pedido.
O dono tem um painel protegido por login para ver os pedidos (Novos → Fazendo → Pronto →
Saiu para entrega) e para cadastrar produtos, fotos e preços.

## Rodar no seu computador (para testar)

```bash
python -m venv venv
venv\Scripts\activate           # Windows   (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```

- Cardápio dos clientes: http://localhost:5000
- Painel do dono: http://localhost:5000/admin

**Primeiro acesso:** na primeira vez que o sistema sobe, ele cria o usuário `admin` com
uma **senha aleatória e mostra no terminal** (uma caixa "PRIMEIRO ACESSO AO PAINEL").
Anote. Para escolher a sua própria senha já na primeira vez, defina antes de rodar
(mínimo 10 caracteres):

```powershell
$env:ADMIN_SENHA = "uma-senha-forte-aqui"      # Windows (PowerShell)
python app.py
```

```bash
ADMIN_SENHA="uma-senha-forte-aqui" python app.py    # Linux / Mac
```

Para abrir no celular, na mesma rede Wi-Fi: rode com `HOST=0.0.0.0` (no PowerShell:
`$env:HOST="0.0.0.0"`) e acesse `http://IP-DO-COMPUTADOR:5000`.

## Vindo da versão anterior (menu.json / pedidos.jsonl)?

Copie para dentro da pasta nova, **antes de rodar pela primeira vez**: o seu `menu.json`,
o `pedidos.jsonl` e a pasta `static/uploads/` da versão antiga. Na primeira execução o
sistema importa tudo para o banco (produtos, preços, esgotados, fotos, pedidos e a
numeração das senhas) e renomeia os arquivos antigos para `menu.json.migrado` e
`pedidos.jsonl.migrado`. Nada é apagado, inclusive as fotos originais. Se não houver
`menu.json`, o cardápio de exemplo (`seed_menu.json`) é carregado.

## O que mudou nesta versão

| Tema | Como ficou |
| --- | --- |
| **Banco de dados** | SQLite (`dados/cardapio.db`), com regras de integridade no próprio banco, preços em centavos (sem erro de arredondamento) e toda gravação em transação. Testado com 6 processos gravando ao mesmo tempo: nenhuma senha repetida, nenhum erro. |
| **Segurança do painel** | Login com senha protegida por hash (scrypt), sessão com prazo de 12 h, proteção CSRF, bloqueio de tentativas, papéis (administrador e cozinha), cabeçalhos de segurança e CSP. Detalhes abaixo. |
| **Celular primeiro** | O CSS foi escrito para tela pequena e só *acrescenta* regras em telas maiores. Botões com área de toque grande, carrinho que sobe da base da tela, campos que não dão zoom no iPhone, respeito ao "notch". |
| **Produção** | Gunicorn (Linux/Mac) ou Waitress (Windows), com configuração pronta. |
| **Velocidade** | Fotos viram WebP (miniatura de até 400 px nos cards), CSS/JS/fotos ficam 1 ano no cache do celular, o menu usa ETag (resposta 304 sem corpo) e as respostas são comprimidas. |

## Painel do dono

Acesse `/admin`. Há duas abas:

- **Pedidos**: Novos, Fazendo, Pronto e Saiu para entrega, com contador, aviso sonoro e
  atualização automática a cada 10 s. Pedidos de entrega mostram telefone (toque para
  ligar) e endereço (abre no mapa). Entregues e cancelados saem da tela, mas continuam
  salvos no banco.
- **Cardápio** (só administrador): cadastrar, editar e excluir produtos, criar categorias,
  enviar foto e marcar *esgotado / disponível*. O cliente vê a mudança em até 30 s, sem
  recarregar, e o servidor recusa pedidos com item esgotado.

**Fotos:** envie JPG, PNG, WEBP ou GIF (até 5 MB). O sistema confere se é mesmo uma imagem,
corrige a rotação de fotos de celular, **remove dados de localização (EXIF)** e converte
para WebP em duas versões (1000 px e 400 px). A foto fica em `static/uploads/`.

### Usuários e papéis

| Papel | Pode |
| --- | --- |
| `admin` | Tudo: pedidos, cardápio, fotos |
| `cozinha` | Só a aba Pedidos (ver e mudar o status) |

```bash
python manage.py listar
python manage.py criar-usuario maria --papel cozinha     # pede a senha, sem mostrar
python manage.py trocar-senha admin                      # derruba quem estava logado
python manage.py desativar maria                         # perde o acesso na hora
python manage.py desbloquear                             # zera o bloqueio de tentativas de login
python manage.py backup                                  # cópia segura do banco + fotos (guarda os 14 mais novos)
python manage.py anonimizar --dias 180                   # LGPD: apaga dados pessoais de pedidos encerrados há mais de 180 dias
```

Nunca deixe o painel aberto no computador compartilhado do balcão com o usuário `admin`:
crie um usuário `cozinha` para quem só mexe nos pedidos.

## Colocar na internet (produção)

**Não use `python app.py` na internet** (é só para testes). Use:

```bash
# Linux / Mac (também na maioria das hospedagens: Render, Railway, VPS...)
gunicorn wsgi:app -c gunicorn.conf.py

# Windows (o Gunicorn não roda no Windows)
python servir.py
```

O `Procfile` já está pronto para hospedagens que o usam. A porta padrão é `8000`
(`PORT=...` para mudar).

Em produção, coloque **na frente** um proxy com HTTPS (Caddy, Nginx ou o próprio da
hospedagem). Ele cuida do certificado, de conexões lentas e de timeouts; o Gunicorn sozinho
não é feito para ficar exposto direto. Exemplo com Caddy (HTTPS automático):

```
cardapio.seudominio.com.br {
    reverse_proxy 127.0.0.1:8000
}
```

Com o proxy, defina **`TRUST_PROXY=1`** (e rode o Gunicorn em `HOST=127.0.0.1`). Sem isso o
sistema enxerga o IP do proxy no lugar do IP do cliente, e o bloqueio de tentativas de
login e o limite de pedidos passam a valer para *todo mundo junto*.

### Variáveis de ambiente

| Variável | Para que serve | Padrão |
| --- | --- | --- |
| `ADMIN_USUARIO` / `ADMIN_SENHA` | Usuário e senha do administrador criado na 1ª execução (senha com 10+ caracteres) | `admin` / senha aleatória mostrada no terminal |
| `TRUST_PROXY` | `1` quando houver proxy na frente (Nginx, Caddy, hospedagem) | desligado |
| `COOKIE_SECURE` | `1` força o cookie de login só por HTTPS (já é automático quando o acesso é HTTPS) | automático |
| `SECRET_KEY` | Chave das sessões (32+ caracteres). Se ausente, é gerada e guardada no banco | gerada |
| `DATABASE_PATH` | Onde fica o arquivo do banco | `dados/cardapio.db` |
| `UPLOADS_DIR` | Onde ficam as fotos | `static/uploads` |
| `MAX_PEDIDOS_POR_10MIN` | Pedidos por IP a cada 10 min (o padrão é folgado, porque o Wi-Fi da lanchonete é um IP só) | `60` |
| `EVOLUTION_API_KEY` | Chave da Evolution API (WhatsApp). Sem ela, nenhuma mensagem é enviada | — |
| `EVOLUTION_BASE_URL`, `EVOLUTION_INSTANCE` | Endereço da Evolution e nome da instância | `http://127.0.0.1:8080`, `lanchonete` |
| `HOST`, `PORT` | Endereço e porta do servidor | `0.0.0.0`, `8000` |
| `WEB_CONCURRENCY`, `THREADS` | Processos e threads do Gunicorn | `2`, `4` |

**Atenção ao disco:** o banco (`dados/`) e as fotos (`static/uploads/`) precisam ficar em
disco **persistente**. Em hospedagens que apagam o disco a cada deploy (Render e Railway
no plano grátis, Heroku), aponte `DATABASE_PATH` e `UPLOADS_DIR` para um volume
persistente, senão os pedidos e as fotos somem.

## Segurança: o que está protegido

- **Tudo em `/admin` exige login**: sem sessão válida, páginas redirecionam para o login e
  a API responde 401. Cada rota declara o papel exigido; o usuário é relido do banco a cada
  requisição, então desativar alguém ou trocar a senha derruba a sessão na hora.
- **Senhas** guardadas só como hash (scrypt, com sal). Mínimo de 10 caracteres, senhas
  óbvias recusadas. O tempo de resposta é igual para usuário que existe e que não existe.
- **CSRF**: toda alteração (criar, editar, excluir, enviar foto, sair) exige um token que
  só a página do painel conhece.
- **Bloqueio de tentativas de login**: 5 erros do mesmo usuário e IP, ou 20 erros do mesmo
  IP, bloqueiam por 15 minutos.
- **Cookie** `HttpOnly`, `SameSite`, `Secure` em HTTPS, prazo absoluto de 12 h.
- **Cabeçalhos**: CSP rígida (nenhum script ou estilo inline), `X-Frame-Options`, `nosniff`,
  HSTS em HTTPS. As telas do painel nunca ficam no cache do navegador.
- **Texto digitado** (nome, endereço, observação) é sempre mostrado como texto, nunca como
  HTML. Todo SQL usa parâmetros.
- **Preços** são sempre lidos do banco na hora do pedido; o que o navegador envia não conta.
- **Limites**: tamanho de pedido e de requisição, pedidos por IP, fotos (5 MB, dimensões,
  formato conferido pelo conteúdo).

**Lembretes:** o endereço e o telefone dos clientes de entrega são dados pessoais; proteja
o arquivo do banco. E faça backup: `python manage.py backup` e a pasta `static/uploads`.

## WhatsApp (Evolution API)

Suba com `docker compose up -d` depois de criar o `.env` (copie `.env.example` e gere senhas
aleatórias). Use a mesma `EVOLUTION_API_KEY` no ambiente do cardápio. As portas do Postgres e
do Redis não são publicadas, e a API só escuta em `127.0.0.1`. O pareamento por QR code é uma
credencial: não compartilhe a imagem. A Evolution usa WhatsApp não oficial, então há risco de
banimento do número; para volume alto, considere a API oficial (Cloud API).

## Funcionamento, pagamento e entrega

Tudo isso é configurado no painel, aba **Cardápio → Funcionamento e entrega** (só administrador):

- **Modo:** *Sempre aberta* (padrão, ignora horários), *Seguir os horários* (cada dia da semana tem
  seu horário; janelas que passam da meia-noite, como 18:00 às 02:00, funcionam) ou *Fechada agora*.
  Com a loja fechada o cliente vê o cardápio, mas não consegue finalizar (o servidor também recusa).
  O fuso é o de Brasília; para outro, defina `TZ_LOJA` (ex.: `America/Manaus`).
- **Taxa de entrega** (só em pedidos de entrega, entra no total) e **pedido mínimo** (sobre os itens).
- **Forma de pagamento** obrigatória: Pix, Cartão ou Dinheiro (com "troco para quanto?"). É só
  um registro: o pagamento acontece na retirada/entrega. Aparece no cartão do pedido, na comanda e
  no Financeiro. Não há cobrança online (Pix com QR exigiria uma chave Pix/provedor de pagamento).

## Comanda e pedidos esquecidos

- O botão **🖨 Comanda** em cada pedido abre uma página de 80 mm pronta para impressora térmica ou comum.
- Pedidos que ficam na fila por mais de 12 horas geram um aviso no topo do painel (só administrador),
  com dois botões: *Marcar como entregues* (entram no faturamento) ou *Cancelar todos*. Os clientes
  **não** são avisados por WhatsApp nesse caso.

## Financeiro

Aba **💰 Financeiro** (só administrador): faturamento, ticket médio, cancelados, por dia, balcão x
entrega, por forma de pagamento, mais vendidos e horários de pico, com comparação com o período
anterior e planilha CSV. Só pedidos marcados como *Entregue* contam como faturamento.

## Backup automático (Windows)

`python manage.py backup` copia o banco e as fotos para `dados/backups/`. Para rodar todo dia às 4h,
abra o PowerShell **na pasta do projeto** e rode uma vez:

```powershell
schtasks /Create /SC DAILY /ST 04:00 /TN "Backup Cardapio" /TR "$PWD\backup_diario.bat"
```

Depois copie a pasta `dados/backups/` para um pendrive ou serviço de nuvem de vez em quando: backup
no mesmo computador não protege contra pane do disco. Restaurar = parar o sistema e copiar um
`cardapio-*.db` para `dados/cardapio.db` (e descompactar o `fotos-*.zip` em `static/uploads/`).

## Dados dos clientes (LGPD)

Nome, telefone e endereço são dados pessoais. Para apagá-los de pedidos antigos, mantendo valores e
itens (o financeiro continua correto): `python manage.py anonimizar --dias 180` (mínimo 30 dias).
Pode ser agendado como o backup.

## E o PostgreSQL?

Para uma lanchonete, o SQLite é mais que suficiente (milhares de pedidos por dia) e não
exige instalar nem administrar nada. Considere o PostgreSQL se for rodar em **vários
servidores ao mesmo tempo** ou com uma rede de lojas. Todo o acesso ao banco está isolado
em `db.py`, então a troca fica concentrada em um arquivo.

## Estrutura

```
cardapio/
├── app.py              # rotas, login, CSRF, cabeçalhos, cache
├── db.py               # esquema do banco, migração, consultas
├── seguranca.py        # senhas, chave secreta, usuário inicial
├── imagens.py          # converte fotos para WebP, miniaturas, limpeza
├── manage.py           # usuários, backup e anonimização pela linha de comando
├── backup_diario.bat   # backup para agendar no Windows
├── whatsapp.py         # envio de mensagens (Evolution API)
├── wsgi.py             # entrada para o Gunicorn
├── servir.py           # servidor de produção para Windows (Waitress)
├── gunicorn.conf.py    # configuração do Gunicorn
├── Procfile            # para hospedagens
├── seed_menu.json      # cardápio de exemplo (só é lido se o banco estiver vazio)
├── dados/              # cardapio.db (criado sozinho)
├── templates/          # index.html, login.html, admin.html, comanda.html
└── static/             # style.css, admin.css, app.js, admin-*.js, comanda.*, uploads/
```

## Personalizar

- **Cores:** variáveis no topo de `static/style.css` (`--vermelho`, `--amarelo`...).
- **Nome das abas de pedidos:** lista `ABAS_STATUS` em `static/admin-pedidos.js`.
- **Nome da lanchonete:** no painel, aba Cardápio, campo "Nome da lanchonete".

## Novidades (painel)

- **Aviso de WhatsApp no painel**: um selo no topo mostra 🟢/🔴 e, se a Evolution estiver fora do ar, desconectada ou sem chave, aparece uma faixa vermelha explicando. Confere a cada 30 s.
- **QR code do cardápio**: aba Cardápio → "Abrir cartaz para imprimir". Defina `PUBLIC_URL` (ex.: `https://meucardapio.com.br`) para o endereço já vir certo; dá para editar na própria página. O gerador de QR é próprio (`static/qrcode.js`), sem bibliotecas externas.
- **Custo e lucro**: o formulário de produto tem o campo "Custo" (opcional, só você vê). O Financeiro mostra lucro estimado, margem e lucro por produto nos mais vendidos. O custo é gravado no momento da venda — mudar o custo depois não altera vendas antigas. A taxa de entrega não entra no lucro.

## Testes automáticos

```
pip install pytest            # opcional: opencv-python-headless numpy (teste do QR)
python -m pytest tests -q
```
Usam banco e pasta de fotos temporários; o seu `cardapio.db` não é tocado.

## Endereço de entrega em campos separados

O cliente preenche **rua, número, bairro, complemento e ponto de referência** (com opção "sem número"). Cada campo é validado no navegador (mensagem junto ao campo) e de novo no servidor. O texto completo do endereço é montado automaticamente e continua sendo usado no link do mapa e na comanda, que agora mostra rua/número, bairro, complemento e referência em linhas separadas. Também:

- máscara automática no telefone `(87) 99999-9999`;
- "Lembrar meus dados neste aparelho": nome, telefone e endereço ficam salvos só no navegador do cliente (desmarcando, apaga);
- Financeiro → "Entregas por bairro" (pedidos e total por bairro);
- LGPD: a anonimização apaga rua, número, complemento e referência (o bairro é mantido só para as estatísticas).
- páginas antigas em cache que ainda enviam o endereço num campo só continuam funcionando.

## Taxa por bairro, alerta do dono e tela da cozinha

- **Taxa por bairro** (Cardápio → Funcionamento e entrega): escolha "Só os bairros da lista" e cadastre um por linha, ex.: `Centro; 5,00` (sem valor usa a taxa única; `0` = entrega grátis). O cliente passa a escolher o bairro numa lista que já mostra a taxa; bairros fora da lista são recusados no servidor. Maiúsculas e acentos não importam ("sao jose" = "São José").
- **Alerta do dono**: no mesmo cartão, preencha "WhatsApp do dono". A cada pedido novo ele recebe uma mensagem com número, tipo, itens e total (usa a mesma Evolution API dos avisos aos clientes; se o selo do painel estiver vermelho, o alerta também não sai). Vazio = desligado.
- **Tela da cozinha** (`/admin/cozinha`, link "🍳 Tela da cozinha" no topo do painel; funciona para admin e cozinha): letras grandes, colunas Novos / Fazendo / Prontos, botões "Começar" e "Pronto", cor amarela após 15 min e vermelha após 30 min, som para pedido novo, botão de tela cheia e tela sempre acesa. Não mostra telefone nem endereço.

## Adicionais, link de acompanhamento e aviso de atraso

- **Adicionais e retiradas** (Cardápio → formulário do produto): em "Adicionais" escreva um por linha, ex.: `Bacon; 3,00` (sem valor = grátis); em "O cliente pode tirar", um ingrediente por linha (`Cebola`). Produtos com opções mostram "a partir de R$ X" e o botão "Escolher", que abre uma janela com as opções. O mesmo lanche com opções diferentes vira linhas separadas no carrinho. O servidor confere cada opção contra o cadastro e calcula o preço (o cliente não consegue extra grátis). As opções aparecem no cartão do pedido, na comanda, na tela da cozinha e na página de acompanhamento. *Obs.:* o custo informado do produto não inclui o custo dos adicionais, então o lucro estimado conta o preço dos adicionais como lucro integral.
- **Link de acompanhamento**: cada pedido ganha um link único e impossível de adivinhar (`/acompanhar/<código>`), enviado na mensagem de WhatsApp de "pedido recebido" e mostrado na confirmação. A página mostra as etapas e se atualiza sozinha; não exibe nome, telefone, endereço nem observação. Defina `PUBLIC_URL` (ex.: `https://meucardapio.com.br`) para o link sair com o endereço público certo; sem isso usa o endereço pelo qual o pedido foi feito. Pedidos criados antes desta versão não têm link.
- **Aviso de atraso** (Cardápio → Funcionamento): "Pedido atrasado após __ minutos" (padrão 30; 0 desliga). Pedido novo ou em preparo há mais tempo que isso fica com borda vermelha e a etiqueta "⏰ Atrasado" no painel e na cozinha, e o WhatsApp do dono recebe um aviso, uma única vez por pedido (conferência a cada minuto, só em pedidos das últimas 6 horas). Para desligar o conferidor, defina `SEM_VIGIA=1`.

## Estoque, horário por produto e pedidos agendados

- **Estoque (opcional)**: no formulário do produto, "Estoque". Cada pedido desconta a quantidade (a baixa é atômica: dois clientes ao mesmo tempo não conseguem passar do que existe). Ao zerar, o item some do cardápio sozinho; quando restam 5 ou menos, o cardápio mostra "Restam só N". Cancelar um pedido devolve as unidades (e reativa o item que tinha zerado). Para reativar um item zerado, informe o novo estoque ao editá-lo. Vazio = sem controle. O estoque não é divulgado ao público, a não ser o aviso de poucas unidades.
- **Vender só entre** (opcional): janela de horário do produto, ex.: açaí das 14:00 às 22:00 (aceita janela que passa da meia-noite). Fora dela o item aparece como "Fora do horário — Disponível das X às Y" e o servidor recusa o pedido. A chave "Disponível/Esgotado" do painel continua sendo a pausa manual.
- **Pedidos agendados** (Cardápio → Funcionamento e entrega → "Aceitar pedidos agendados"): o cliente escolhe "Agendar" e um horário de 30 em 30 min nas próximas 48 h, **só dentro do funcionamento** (com a loja fechada agora, só dá para agendar). A lista de horários é gerada no servidor, que confere de novo no pedido. O pedido fica numa faixa "📅 Agendados" no painel e só entra em "Novos" (e na cozinha, com som) X minutos antes do horário (padrão 40; configurável). "Começar já" libera na hora. O contador de atraso e o aviso de "pedido esquecido" contam a partir da liberação. A confirmação do WhatsApp, o link de acompanhamento, a comanda e o alerta do dono mostram o horário combinado. No Financeiro o pedido conta no dia em que foi feito.

## Arquivo .env e roteiro de colocação no ar

O arquivo `.env` agora é lido automaticamente (`ambiente.py`) por `servir.py`, `gunicorn`, `python app.py` e `manage.py`; o que já estiver definido no terminal tem prioridade. Para o passo a passo completo (chaves, HTTPS, iniciar com o Windows, backup e checklist de pedido de teste) veja **COLOCAR_NO_AR.md**. `iniciar_cardapio.bat` liga o servidor e religa se ele cair.

## Estoque geral (contagem de pães)

Em vez de controlar o estoque lanche por lanche, crie uma contagem única na aba **Cardápio → 🍞 Estoque geral** (ex.: "Pão", 100 unidades, avisar em 10).

- **Ligar aos produtos:** use "Fazer todos os produtos de [categoria] gastarem N de [estoque]" para ligar uma categoria inteira de uma vez, ou escolha o estoque no formulário de cada produto ("Gasta do estoque geral" + quantidade por unidade; ex.: combo duplo = 2 pães).
- **Venda:** cada unidade vendida desconta do estoque; um pedido que não cabe é recusado inteiro, sem descontar nada.
- **Acabou o pão:** todos os produtos ligados saem do cardápio sozinhos e voltam quando você repõe (campo da quantidade, botão +10).
- **Cancelar pedido** devolve o pão.
- **Aviso:** quando chegar ao mínimo, o dono recebe um WhatsApp (uma vez só, até repor).
- A contagem aparece em chips no topo da aba Pedidos (também para a cozinha).
- Pode combinar com o estoque por produto: vale o que acabar primeiro.
- **Escolher lanche por lanche:** em cada estoque, o botão **Escolher lanches** abre a lista de todos os produtos; marque os que gastam dele e diga quanto cada um gasta. Salvar substitui a escolha anterior (quem foi desmarcado deixa de usar o estoque).

## Excluir categoria

Na aba Cardápio, cada categoria tem o botão **Excluir categoria**. Ele apaga a categoria e os produtos dela (pede confirmação). Pedidos antigos e o financeiro não mudam, pois guardam uma cópia dos itens.

**Adicionais:** o preço de cada adicional soma ao lanche. Escreva `Bacon; 3,00` (também funciona `Bacon: 3,00`, `Bacon - R$ 3,00` e `Bacon 3,00`). Adicionais antigos cadastrados com o preço dentro do nome são corrigidos automaticamente.

**Planilha do Financeiro:** o botão "Baixar planilha (Excel)" gera um .xlsx já formatado (colunas largas, valores em R$, filtros, linha de título congelada, situação colorida e totais de concluídos/cancelados). O "CSV simples" continua disponível. Precisa do pacote `openpyxl` (`pip install -r requirements.txt`).

## Abas do painel

Pedidos · 🍔 **Cardápio** (categorias, produtos, estoque geral) · 🏪 **Loja** (nome, funcionamento e entrega, QR code) · 💰 Financeiro.

## Cupons, avaliações, app, mesas e histórico

- **Cupons (aba Loja):** crie códigos como `PRIMEIRA10` (% ou R$), com pedido mínimo, validade, limite de usos, "1 uso por cliente" e "só primeira compra". O desconto vale só sobre os lanches (não sobre a taxa). Cancelar o pedido devolve o uso. O cliente digita o código no carrinho e vê o desconto na hora; o servidor confere tudo de novo ao fechar. Tentativas de adivinhar códigos são limitadas.
- **Avaliações:** depois que o pedido é concluído, o link de acompanhamento mostra 5 estrelas e um comentário opcional (uma avaliação por pedido). A média, a distribuição e os últimos comentários aparecem na aba Financeiro, no período escolhido.
- **Instalar como app (PWA):** o cardápio tem botão "📲 Instalar o app" (Android/Chrome) e dica para iPhone. Funciona com `https://` (ou `localhost`). O serviço em segundo plano nunca guarda pedidos, painel nem acompanhamento.
- **Tempo estimado:** o cliente vê "pronto em cerca de X min". Vale o número da aba Loja até haver 5 pedidos prontos; depois o sistema usa a média real e soma um pouco por pedido na fila. O acompanhamento mostra a previsão restante do pedido.
- **Pedidos por mesa:** na aba Loja, informe a quantidade de mesas e abra "Cartazes das mesas" para imprimir um QR por mesa. O cliente escaneia, pede e o pedido chega como "Mesa N" no painel, na cozinha e na comanda (sem endereço nem taxa).
- **Histórico (aba 📜 Histórico):** registra quem mudou preço, estoque, produtos, categorias, cupons, configurações e o andamento dos pedidos (guarda as últimas 20 mil anotações). Dá para filtrar por área e buscar por texto ou usuário.

## Relatório diário, fidelidade, pedir de novo, nota baixa e cupom por link

- **Relatório diário (aba Loja):** informe o WhatsApp do dono e o horário (ex.: 21:30). Todo dia, nesse horário, chega o resumo: faturamento, pedidos, ticket médio, mais vendidos, descontos dados, nota média, cancelados e pedidos em andamento. Sai uma vez por dia (o servidor precisa estar ligado). O botão "Enviar o de hoje agora" testa.
- **Fidelidade (aba Loja):** "a cada N pedidos concluídos, X% de desconto". O cliente é reconhecido pelo telefone; o prêmio entra sozinho no pedido seguinte, soma com cupom, e o carrinho mostra "Faltam N pedidos…" ou "Você ganhou…". Cancelar o pedido devolve o prêmio.
- **Pedir de novo:** o cardápio mostra o botão "🔁 Pedir de novo" com o último pedido feito naquele aparelho (itens indisponíveis ficam de fora).
- **Clientes sumidos (aba Loja):** lista quem já comprou e não pede há 14/21/30/60/90 dias. Você edita a mensagem (`{nome}`, `{loja}`, `{link}`), pode incluir um cupom e envia um cliente por vez; o mesmo cliente não recebe de novo por 14 dias. Mande só para clientes seus e com moderação (o WhatsApp pode bloquear números que mandam mensagens demais).
- **Alerta de nota baixa:** nota 1 ou 2 gera na hora um WhatsApp para o dono, com pedido, cliente e comentário.
- **Cupom por link:** `https://seusite/?cupom=PRIMEIRA10` guarda o cupom e o aplica quando o cliente abre o carrinho. Na lista de cupons, "Copiar link" já entrega o endereço pronto (usa `PUBLIC_URL`).

## Painel como aplicativo (Windows)

Dê dois cliques em `criar_atalho_painel.bat`: ele cria na área de trabalho o atalho **Painel da Lanchonete** (com ícone). Ao abrir o atalho, o painel abre numa janela própria, sem barra de endereços, como um aplicativo; se o cardápio ainda não estiver ligado, ele liga sozinho (numa janela minimizada que **não deve ser fechada**). O arquivo `abrir_painel.bat` faz esse trabalho e usa o Edge (ou o Chrome). Também dá para instalar pelo próprio navegador: no painel, o botão **📲 Instalar como app**.

## Ponte WhatsApp sem Docker

Pasta `ponte_whatsapp/` (Node.js + Baileys) imita a Evolution API. Veja a seção 3 de `COLOCAR_NO_AR.md`. Teste da camada HTTP: `node ponte_whatsapp/teste.js`. Não testada contra o WhatsApp real no ambiente de desenvolvimento.
