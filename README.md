# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

O fluxo implementado hoje usa comandos explícitos para controles acessíveis da tela,
digitação e atalhos de abertura. Movimento livre do mouse, envio guiado de e-mails,
interpretação por LLM e interrupção independente por voz são objetivos futuros.

## Feedback, ajuda e recuperação por voz

A resposta é exclusivamente textual. A barra separa o estado **Ouvindo…** ou
**Transcrevendo…** da orientação da etapa (por exemplo, “Diga confirmar ou cancelar”).
Após cinco segundos de transcrição, informa que ainda está processando, sem estimar
porcentagem. O resultado continua visível; uma orientação separada indica quando o
reconhecimento está pronto para outro “Hey Jarvis”.

Uma frase transcrita que não corresponda a um comando recebe uma explicação e
exemplos, em vez de simplesmente aparecer como se tivesse sido executada. Silêncio
e transcrição vazia têm mensagens próprias. Há **uma nova captura automática**;
se também falhar, a barra pede uma nova ativação. Nenhuma ação já solicitada é repetida.

Nas etapas de escolha, confirmação e ditado, uma resposta inválida ou sem fala
permite uma repetição. O prazo de 30 segundos de cada etapa não é renovado por
repetições ou ajuda. Troca de janela, alvo inválido e expiração cancelam a proposta.
Atalhos não encontrados também permitem uma nova resposta no prazo original.

Diga **“ajuda”**, **“o que posso dizer”** ou **“quais comandos”** para exemplos do
contexto atual. A mesma ajuda está no menu da bandeja. Durante o ditado, essas
palavras são texto literal. “Cancelar”, “não” e “parar” mantêm o comportamento de
cancelamento existente.

Falhas de conexão são recuperadas com esperas de 1, 2, 5, 10 e 15 segundos; as
seguintes usam 15 segundos. A abertura do microfone é repetida a cada 5 segundos,
sempre para o dispositivo selecionado. A barra mostra “Reconectando…” ou “Microfone
indisponível…”. Fechar o aplicativo interrompe as esperas.

**Limitação:** “parar” por voz depende da captura e transcrição; não é um comando
de emergência independente durante inferência ou execução nativa. O botão cancela
imediatamente a interação local. Uma ação já enviada a outro aplicativo pode terminar
e não é desfeita pelo cancelamento.

## Speakbar integrada ao reconhecimento de voz

Na raiz do projeto, instale as dependências do host (interface e reconhecimento).
No PowerShell, usando a venv existente:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up --build -d
.venv\Scripts\python.exe main.py
```

O Docker Desktop precisa estar em execução antes de iniciar os containers.
A transcrição em streaming exige a API e o novo serviço `realtime` disponíveis.
Na primeira inicialização, o `realtime` baixa o Whisper `small` para o volume de cache
e aquece o modelo antes de aceitar conexões. Acompanhe com `docker compose logs -f realtime`;
`Application startup complete` indica que está pronto. Os próximos inícios reutilizam o cache.
A demonstração `--demo` continua funcionando sem Docker.

- Aguarde “Aguardando comando” e diga **“hey jarvis”** ou clique no botão de ondas.
- A barra mostra “Ouvindo…” durante toda a fala.
- Após aproximadamente **2 segundos de silêncio**, a captura termina e a barra mostra
  “Transcrevendo…”. O servidor transcreve o áudio completo uma única vez e a interface interpreta o comando.
  Sem fala, a interação termina após **10 segundos**; cada comando dura no máximo **30 segundos**.
- O resultado da interação permanece até o próximo comando.
  A wake word volta a ficar ativa enquanto esse resultado continua visível.
- A barra expande para até três linhas. Textos maiores têm rolagem por mouse e teclado.
  Arraste a área central para reposicioná-la; a posição é salva quando “Lembrar posição” está ativado.
- Clique novamente nas ondas durante captura ou transcrição para cancelar.
  Uma inferência já iniciada pode terminar no servidor, mas seu resultado será descartado.
- X, Alt+F4 e “Sair” encerram a interface e a captura de áudio. Durante uma operação em
  andamento, a barra mostra “Encerrando…” até liberar os recursos; ela não fica minimizada.
- Tab navega entre voz, fechar e texto; Enter/Espaço ativam os botões.
  Com o texto focado, setas e PageUp/PageDown permitem navegar pela transcrição.
- Falhas de áudio ou conexão têm recuperação automática e permitem nova tentativa pelo botão.
  Em caso de desconexão, a barra informa a falha sem apresentar uma transcrição incompleta.
  Uma interação sem fala tem uma nova captura automática antes de voltar à escuta da wake word.

No modo padrão, o áudio fica **somente em memória**, sem novos WAVs em `recordings/`
ou uploads gravados no servidor. As gravações anteriores são preservadas.
Controles da janela atual podem ser acionados após confirmação por voz, usando as APIs
de acessibilidade do Windows/macOS. A interpretação por LLM continua fora desta etapa.
A aparência preserva transparência e gradiente, sem desfoque nativo.

### Modos de execução

Com a venv ativada:

```bash
python main.py                       # interface e reconhecimento real
python -m speakbar                   # mesma interface integrada
python main.py --headless            # reconhecimento e resultado no terminal
python -m speakbar --demo            # demonstração sem microfone, modelos ou API
```

`python main.py --demo` também abre a demonstração. Para usar apenas a demonstração,
basta instalar `speakbar/requirements.txt`; para apenas o terminal, basta
`wakeword/requirements.txt`. O requirements da raiz referencia ambos, sem duplicá-los.
Não execute simultaneamente duas instâncias reais que disputem o mesmo microfone.
O serviço `realtime` atende uma conexão de reconhecimento por vez.

### Rolagem por voz (Windows e macOS)

Diga **Hey Jarvis** e **“role para baixo”** ou **“role para cima”**. A rolagem é
executada diretamente, sem confirmação. Também aceita “rolar para baixo”, “descer
a tela”, “subir a tela” e as respectivas variações de direção.

| Comando | Intensidade |
|---|---|
| Role um pouco para baixo / cima | Ajuste pequeno, aproximadamente 15% da área visível. |
| Role para baixo / cima | Movimento normal, aproximadamente metade da área visível. |
| Role uma página para baixo / cima | Movimento maior, aproximadamente 90% da área visível, mantendo sobreposição. |

Quando o aplicativo não informa o tamanho da área visível, é usado o incremento
nativo pequeno ou grande que ele oferece; a distância pode variar.

Quando há posicionamento preciso disponível, o deslocamento é gradual, com início
e término suaves: cerca de **0,45 s** no ajuste pequeno, **0,75 s** no normal e
**1,1 s** em uma página. A distância permanece a mesma. O alvo e a janela são
revalidados a cada passo, e o cancelamento interrompe os passos restantes. Aplicativos
que oferecem apenas incrementos nativos mantêm o movimento definido por eles.

Após cada movimento, a barra mostra o resultado e abre uma captura de continuação:
você tem **cinco segundos para começar a falar**, sem repetir Hey Jarvis.

- **“Mais”** repete a última direção e intensidade.
- **“Para cima” / “para baixo”** muda a direção, mantendo a intensidade.
- Um novo comando completo de rolagem muda também a intensidade.
- **“Parar”**, **“cancelar”** ou silêncio encerra normalmente, sem repetir a captura
  nem apresentar silêncio como erro. Os demais comandos mantêm seus tempos atuais.
- A indicação de início/fim só aparece quando o aplicativo informa esse limite.
  Se ele aceitar o pedido sem permitir observar a posição, a barra informa
  **“Rolagem solicitada ao aplicativo”**.

A área em foco tem prioridade. Sem essa referência, uma única área disponível
é selecionada automaticamente; várias áreas ou uma leitura parcial mostram números
e perguntam **“Qual área você quer rolar?”**. Diga o número para rolar diretamente.
O foco externo é preservado quando o botão da speakbar recebe foco.

Com números de controles visíveis, a rolagem remove os marcadores antigos e faz
uma nova leitura após o movimento. **A numeração pode mudar.** Diga um número novo
para escolher um controle (a ação ainda exige confirmação), ou continue rolando.
Uma confirmação pendente precisa ser concluída ou cancelada antes de rolar.
Durante ditado, “role para baixo” é texto literal. Trocar de janela encerra a sessão.

O suporte usa `ScrollPattern` do UI Automation no Windows e, no macOS, o valor da
barra `AXVerticalScrollBar` de cada `AXScrollArea`, sem roda do mouse ou teclas como
fallback. Aplicativos que não expõem esse padrão, incluindo alguns componentes Qt,
informam indisponibilidade. A versão atual não inclui rolagem horizontal nem contínua.
O teste isolado `.venv\Scripts\python.exe tests/scroll_native_check.py` verifica
duas áreas nativas, as intensidades, direções, limites e cancelamento.

### Acesso à tela integrado à speakbar

O acesso à tela roda no host. As dependências estão incluídas no requirements da raiz;
não é necessário reconstruir os containers para esta integração. No PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

No macOS (Terminal, dentro da raiz do projeto):

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Na primeira execução, o macOS pede para autorizar **Acessibilidade** ao aplicativo que
executa o Python (Terminal, iTerm ou o editor). Autorize em **Ajustes do Sistema >
Privacidade e Segurança > Acessibilidade** e reinicie o J.A.R.V.I.S.; o microfone também
precisa ser autorizado para esse aplicativo. A speakbar e os números continuam visíveis
quando outro aplicativo está ativo. Clicar na barra não troca o alvo: a última janela
externa continua sendo usada.

Feche a instância antiga da barra antes de iniciar a atualizada. Mantenha o aplicativo
que deseja controlar em primeiro plano e diga **hey jarvis** (ou use o botão da barra):

- **“Mostrar números”**: exibe marcadores sobre os controles e abre outra captura
  automaticamente. Responda **“sete”**, **“número sete”** ou **“clique no número sete”**.
- **“Clique em pesquisar”**: compara o nome com os controles da janela. Se houver
  vários candidatos, mostra seus números e pede uma escolha.
- **“Clique duas vezes na Lixeira”** ou **“duplo clique no número sete”** (Windows e macOS):
  solicita a abertura padrão de um item ou atalho pela ação nativa `Invoke` (Windows)
  ou `AXOpen` (macOS).
  A abertura é solicitada uma única vez. Itens sem essa ação informam indisponibilidade.
- **“Abrir menu de contexto da Lixeira”** (Windows e macOS): equivalente ao botão direito.
  No macOS, usa a ação `AXShowMenu` do próprio controle, sem atalho de teclado.
  Também aceita **“clique com o botão direito no número sete”**. A barra identifica
  o controle e pede confirmação; então devolve o foco e envia **Shift+F10**, o
  [atalho do Windows para o menu de contexto](https://learn.microsoft.com/en-us/windows/win32/menurc/wm-contextmenu).
  O aplicativo precisa oferecer o menu para esse controle. A ação não escolhe
  automaticamente nenhuma opção do menu.
- Depois da escolha, a barra mostra **“Acionar [nome]? Diga confirmar ou cancelar”**
  e abre outra captura. Diga **“confirmar”** ou **“sim”** para acionar; **“cancelar”**,
  **“não”** ou **“parar”** para abandonar a proposta. Não é necessário repetir a wake word.
- **“Esconder números”** remove os marcadores. O botão da barra também cancela uma
  sessão pendente. Cada escolha exige sua própria confirmação.
- **“Digitar olá, mundo”** (Windows e macOS): dê foco ao campo desejado e fale o comando.
  A barra mostra o texto reconhecido e pede **“confirmar”** antes de inseri-lo.
- **“Digitar”** (Windows e macOS): a barra abre uma captura para o texto e, em seguida,
  outra para confirmação. Não é necessário repetir a wake word nessas respostas.
  O campo pode ter sido selecionado diretamente ou com **“clique em pesquisar”**.
- **“Enter”** (Windows e macOS): pede confirmação e envia Enter ao campo de texto ativo.
  Em campos de pesquisa, executa o comportamento de pesquisa do aplicativo; em
  editores de várias linhas, pode inserir uma nova linha. Também aceita **“pressionar enter”**.
- **“Deletar”** (Windows e macOS): pede confirmação e apaga **todo o conteúdo do campo ativo**,
  incluindo texto que já existia antes da última ditagem. Também aceita **“limpar campo”**
  e **“apagar tudo”**. Sem campo editável ativo, solicita que você selecione um.

As perguntas aparecem como texto; não há voz sintetizada nesta etapa. Campos editáveis
recebem foco. A digitação exige um comando explícito e confirmação. Botões, links, caixas de seleção e itens
selecionáveis usam ações nativas, sem fallback de clique por coordenadas.

“Digitar” insere a transcrição final no cursor ou substitui a seleção atual, preservando
o restante do campo. Acentos, caixa e pontuação seguem a transcrição; não há correção
ou interpretação por LLM. O texto é enviado como Unicode literal, sem usar a área de
transferência ou interpretar palavras como atalhos. Não pressiona Enter nem envia
formulários. Mudança de campo ou perda de foco interrompe o envio; confira qualquer
texto já inserido antes de repetir. Sem campo editável ativo, a barra pede para selecionar um.

Para pesquisar por voz: selecione o campo, diga **“digitar previsão do tempo”** e
**“confirmar”**; depois inicie uma nova captura pelo botão ou **“hey jarvis”**, diga
**“Enter”** e confirme. Para limpar a busca, diga **“Deletar”** e confirme. Esses
comandos preservam o campo externo quando a speakbar recebe foco e revalidam o alvo
antes da ação. Durante a captura do texto de “digitar”, Enter e Deletar são tratados
como palavras a inserir. A limpeza usa `ValuePattern.SetValue("")` quando disponível,
ou Ctrl+A e Backspace no editor validado quando só há suporte de texto. No macOS, o
texto é enviado por eventos Quartz Unicode, o campo recebe `AXValue` vazio na limpeza
(ou Cmd+A e Delete) e o aplicativo alvo é trazido para a frente antes de qualquer tecla.

O alvo é a última janela externa em foco, preservada quando o botão da speakbar recebe
foco. No Windows, eventos de mudança de foco são acompanhados na thread do backend,
incluindo transições rápidas antes do clique na barra, usando
[SetWinEventHook](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook).
Trocar de janela ou alterar a posição, o nome ou a disponibilidade de um alvo
invalida a sessão. Os controles marcados são revalidados periodicamente e antes da ação.
Não há nova tentativa automática de uma ação; um pedido já iniciado no aplicativo não
pode ser desfeito pelo cancelamento. Ausência de fala ou resposta inválida permite uma
nova tentativa na etapa; uma segunda falha cancela a proposta.

A lista usa profundidade **32** e até **3000 elementos visitados** por padrão, para
alcançar controles de páginas web aninhadas. O limite de tempo de leitura é 5 segundos;
quando um limite é atingido ou um ramo está inacessível, a barra informa que a lista é
parcial. Para aplicativos com árvores
maiores, ajuste antes de iniciar o host:

```powershell
$env:SCREEN_MAX_DEPTH = "48"
$env:SCREEN_MAX_ELEMENTS = "5000"
.venv\Scripts\python.exe main.py
```

`SCREEN_MAX_DEPTH` aceita inteiros >= 0; `SCREEN_MAX_ELEMENTS`, de 1 a 10000. São
numerados no máximo 200 controles por leitura. A sessão expira após 30 segundos sem
concluir a etapa; pedidos em execução têm limite de espera de 90 segundos. Uma chamada
nativa lenta não bloqueia a interface, mas pode terminar no aplicativo depois de expirar.

No Windows, a integração utiliza UI Automation (`uiautomation`). Aplicativos que não
expõem controles ou que executam com privilégios diferentes podem recusar acesso.
Nesses casos, a barra informa a falha e o reconhecimento de voz continua disponível.
Na área de trabalho, a leitura resolve o host dos ícones (`Progman`/`WorkerW`), em vez
de ler apenas a janela do papel de parede, e inclui a barra de tarefas principal e
as barras de outros monitores. Lixeira, atalhos, Iniciar e controles visíveis da
barra de tarefas compartilham a mesma numeração. Os retângulos são tratados
separadamente para cada host; controles de outro monitor não são cortados pelo
retângulo do desktop principal. A passagem entre o fundo, os ícones e a barra de
tarefas preserva a sessão; entrar em outra janela de aplicativo a invalida.

Para controlar a área de trabalho, use **Win+D**, clique numa região vazia dela e
diga **“mostrar números”**. Também pode usar o botão da speakbar: o aplicativo
minimizado anteriormente deixa de ser usado como alvo. Depois diga o número,
**“confirmar”** para executar ou **“cancelar”** para abandonar a proposta. O botão
Iniciar pode ser escolhido diretamente com **“clique em iniciar”** enquanto o alvo
é o desktop. Ícones usam seleção nativa no clique simples; para abrir o atalho,
diga **“clique duas vezes em [nome ou número]”**, seguido de **“confirmar”**.
Para suas opções, diga **“abrir menu de contexto de [nome ou número]”** e confirme.
Elementos ocultos, inclusive de barras de tarefas recolhidas, não recebem marcadores.

Em navegadores, são listados controles
acionáveis da página expostos por acessibilidade, além dos controles do navegador.
Textos, imagens sem ação e conteúdo fora da área visível não recebem números.
Se um navegador Chromium continuar expondo apenas sua interface, verifique o suporte
de acessibilidade da página: o Chrome permite ativá-lo em `chrome://accessibility` ou
ao iniciar com `--force-renderer-accessibility`, conforme a
[documentação do Chromium](https://www.chromium.org/developers/design-documents/accessibility/).
Não é necessário esse ajuste para páginas que já expõem os controles. Variáveis
`SCREEN_MAX_*` configuradas anteriormente continuam prevalecendo sobre os novos padrões.
No macOS, a leitura usa a AX API: botões, links, caixas, menus e campos de texto, além
de linhas de listas e tabelas (seleção por `AXSelected`). Em navegadores Chromium, o
J.A.R.V.I.S. pede ao app que exponha a página (`AXManualAccessibility`). O Linux
mantém a transcrição, mas o acesso à tela não está implementado.

Para inspecionar a janela sem microfone, use o módulo independente:

```powershell
.venv\Scripts\python.exe -m screen_access --delay 5 --clickable --show-frames
.venv\Scripts\python.exe -m screen_access --delay 5 --press
```

O segundo comando recebe a escolha e a confirmação pelo terminal; realiza no máximo
uma ação. Também estão disponíveis `--max-depth`, `--max-elements` e `--include-hidden`.
Elementos ocultos podem ser inspecionados, mas a validação impede acioná-los enquanto ocultos.

Validação automatizada e verificação nativa isolada no Windows:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tests/screen_native_check.py
.venv\Scripts\python.exe -X faulthandler tests/settings_native_check.py
.venv\Scripts\python.exe tests/interaction_visual_check.py
.venv\Scripts\python.exe tests/settings_visual_check.py
```

A verificação nativa abre uma janela descartável e valida Invoke, Toggle, foco,
SelectionItem, menu de contexto, Enter, limpeza de campo e digitação Unicode literal preservando texto existente
após confirmação. A abertura de itens é verificada com padrões simulados; a leitura
nativa confirma a disponibilidade de `Invoke` nos ícones do desktop sem abri-los.
Os testes também cobrem o contrato macOS com APIs
simuladas e coordenadas em 100%, 125%, 150% e monitores com origem negativa.
A validação nativa macOS exige uma máquina macOS com permissão de Acessibilidade;
teste no Finder, Safari/Chrome, Notas e Ajustes do Sistema. Para validar com sua voz, repita o
fluxo de mostrar números, escolher, confirmar e cancelar em Chrome/Edge, Explorer e
Bloco de Notas; verifique especialmente nomes duplicados e mudança de janela durante a confirmação.

## Configurações e atalhos personalizados

Abra pela engrenagem da speakbar, pelo menu do ícone da bandeja ou com:
**“Hey Jarvis” → “Abra as configurações”**. Também aceita “abrir configurações” e
“abra as configurações do Jarvis”. Esse comando abre o menu do J.A.R.V.I.S.; as
configurações do Windows e os Ajustes do macOS são destinos separados no cadastro de atalhos.

O menu tem quatro seções: **Atalhos**, **Voz e microfone**, **Aparência e acessibilidade**
e **Ajuda e diagnóstico**. Enquanto estiver aberto, comandos para aplicativos externos
ficam bloqueados e propostas anteriores são canceladas. A voz aceita apenas comandos do menu.
Uma segunda abertura traz a mesma janela para frente.

Use **Hey Jarvis** antes de cada comando de menu:

- **Ir para atalhos**, **ir para voz e microfone**, **ir para aparência e acessibilidade**
  ou **ir para ajuda e diagnóstico**.
- **Salvar configurações** valida, grava e fecha; **cancelar configurações** ou
  **fechar configurações** descarta o rascunho e fecha.
- **Ativar/desativar alto contraste** e **ativar/desativar lembrar posição**.
- **Tamanho do texto 22**, **tamanho dos números 18**.
- **Tempo de silêncio 3 segundos**, **tempo para começar a falar 15 segundos**,
  **duração máxima 30 segundos**, **limiar de ativação 0,3**.

Números também podem ser ditos por extenso, como “vinte e dois” e “zero vírgula três”.
Valores fora dos limites são recusados. Ajustes ficam no rascunho até salvar; seleção
de microfone e cadastro de atalhos continuam no formulário. Diga **ajuda** para consultar
os comandos. Durante um teste de transcrição, até “salvar configurações” é apenas
texto de diagnóstico e não executa nenhuma ação.

Em **Atalhos**, preencha nome, frase de voz, ação e destino. Pode abrir um site,
aplicativo (`.app` ou imagem `.dmg` no macOS; `.exe` ou atalho `.lnk` no Windows), pasta ou
recurso do sistema: no Windows, Explorador, Configurações, Rede ou Som; no macOS, Finder,
Ajustes do Sistema, Rede ou Som. No macOS, a abertura usa `/usr/bin/open` com argumentos
separados, sem shell. Atalhos de recursos do outro sistema continuam no cadastro, mas não abrem.

Um `.dmg` é montado em modo somente leitura com `hdiutil`, sem aparecer na mesa, e o
aplicativo que está dentro dele é aberto. A barra mostra “Montando a imagem…” e, em
seguida, o resultado; uma imagem já montada é reaproveitada. Imagens sem aplicativo ou
com mais de um `.app` informam a falha, e as que pedem aceite de licença podem não montar
sem interação; nesses casos, instale o aplicativo e cadastre o `.app`.

Os exemplos apenas preenchem o formulário; clique em
**Salvar** para cadastrar. **Adicionar / atualizar** permite organizar vários
cadastros antes de salvar. **Cancelar** descarta o rascunho. **Testar abertura** abre
explicitamente o destino do formulário; no modo `--demo`, nenhuma abertura ocorre.

Exemplo: nome **YouTube**, frase **abrir YouTube**, ação **Site**, destino **youtube.com**.
Depois diga **“abrir YouTube”**, **“executar atalho abrir YouTube”**, ou **“executar atalho”**
e responda **“abrir YouTube”** na captura automática seguinte. Os atalhos executam diretamente,
sem confirmação adicional. Cliques, digitação, Enter e limpeza de campo continuam
exigindo confirmação; rolagem é direta. A comparação das frases ignora acentos, pontuação, caixa e
espaços extras, mas não adivinha uma frase diferente. Frases repetidas ou reservadas,
como “cancelar”, “confirmar” e “abra as configurações”, são recusadas.

### Abrir aplicativos pelo nome (macOS)

Sem cadastro, diga **“Hey Jarvis, abrir Safari”** (também “abra”, “abre”). A busca usa os
aplicativos de `/Applications`, `/System/Applications`, das pastas Utilities, de
`~/Applications` e o Finder, com o nome mostrado pelo Finder: “abrir calendário” e
“abrir calendar” encontram o mesmo app. Nomes separados pela transcrição, como
“chat gpt”, também são reconhecidos. Um atalho cadastrado com a mesma frase tem prioridade.

- Um único aplicativo correspondente é aberto diretamente.
- Com nomes parecidos, a barra numera as opções (“1 Captura de Imagem, 2 Captura de Tela”)
  e abre uma captura: diga o número, uma parte do nome ou **“cancelar”**.
- Sem correspondência, há uma nova tentativa para repetir o nome; a segunda falha encerra.
  O prazo é de 30 segundos. Números isolados (“abrir 3”) não escolhem aplicativos.
- **“Abrir menu de contexto…”** continua sendo um comando de tela. No Windows, a busca por
  aplicativos instalados ainda não está disponível; use os atalhos cadastrados.

A lista é lida novamente a cada minuto, para incluir aplicativos instalados depois.

Na seção de voz, selecione o dispositivo e salve antes de testar um microfone diferente.
**Testar captação** exibe o nível por cinco segundos. **Testar transcrição** mostra a
frase reconhecida sem executar nenhum comando, mesmo “Deletar”. Os testes usam a
thread que já possui o áudio; não abrem uma captura concorrente. A listagem de
dispositivos não cria outra instância do PortAudio na interface: a lista
é consultada na thread de áudio e enviada ao menu de forma assíncrona. A verificação
de regressão no Windows, com captura real e sem Whisper ou gravações, pode ser executada com
`.venv\Scripts\python.exe -X faulthandler tests/settings_native_check.py`.
O menu também permite ajustar os tempos de captura e o limiar da wake word. Limiar menor facilita ativação,
mas pode aumentar chamadas acidentais. Microfones ausentes ou ambíguos exigem nova seleção.

Preferências e atalhos são gravados em `settings.json`, no diretório de configurações
do usuário indicado pelo Qt (no Windows, normalmente
`%LOCALAPPDATA%\J.A.R.V.I.S. Speakbar`). Campos personalizados prevalecem sobre o
ambiente; os demais continuam usando seus valores atuais. A gravação é atômica.
Arquivos inválidos são informados e preservados; ao salvar uma substituição, uma
cópia `settings.invalid-*.json` é mantida. **Restaurar preferências** remove as
personalizações ao salvar e preserva os atalhos. O menu não mantém histórico de áudio
ou transcrições; o teste em modo batch remove seu áudio temporário após processamento.

Salvar aplica a aparência e os limites de tela. Mudanças de áudio reiniciam apenas o
serviço de voz, após liberar o dispositivo. Ao sair, a posição da barra é salva quando
a opção está ativada, e ajustada para um monitor disponível na próxima abertura.
O modo headless continua usando o ambiente. Containers e configuração do Whisper
não são alterados. No macOS, o `settings.json` fica em `~/Library/Preferences/J.A.R.V.I.S. Speakbar`.

### Configuração e modo anterior

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `TRANSCRIPTION_MODE` | `stream` | `stream` envia áudio por WebSocket e transcreve após a fala; `batch` usa gravação e upload antigos. |
| `STREAM_SILENCE_SECONDS` | `2` | Silêncio necessário para finalizar depois de detectar fala. |
| `STREAM_START_TIMEOUT_SECONDS` | `10` | Espera máxima pela primeira fala. |
| `STREAM_MAX_SECONDS` | `30` | Duração máxima da interação, incluindo espera inicial. |
| `STREAM_PRE_ROLL_SECONDS` | `0.8` | Áudio preservado antes da confirmação de voz; aceita valores positivos até 2 segundos. |
| `MICROPHONE_DEVICE_INDEX` | dispositivo padrão | Índice da entrada de áudio do host; consulte `--list-microphones`. |
| `WHISPER_INITIAL_PROMPT` | vazio | Contexto opcional do Whisper no container `realtime`; exige recriar o serviço. |
| `API_URL` | `http://localhost:8000` | Endereço público da API; o cliente deriva a URL WebSocket. |
| `RECORD_SECONDS` | `5` | Duração da gravação, **somente no modo batch**. |

Os três tempos de streaming aceitam números positivos até 30 segundos. Para pausas
maiores durante a fala, aumente `STREAM_SILENCE_SECONDS`, respeitando a duração máxima.
Defina as variáveis antes de iniciar o aplicativo. Exemplo no PowerShell:

```powershell
$env:STREAM_SILENCE_SECONDS = "3"
.venv\Scripts\python.exe main.py
```

Para listar os microfones sem iniciar a captura e selecionar um dispositivo:

```powershell
.venv\Scripts\python.exe main.py --list-microphones
$env:MICROPHONE_DEVICE_INDEX = "1" # substitua pelo índice mostrado na sua máquina
.venv\Scripts\python.exe main.py
```

Os índices podem mudar quando dispositivos são conectados ou desconectados. Sem essa
variável, o aplicativo continua usando o microfone padrão do sistema.

O Whisper usa `small`, temperatura zero e beam 5 para a transcrição final. O início
da fala tem um buffer de 800 ms para preservar sons baixos antes da confirmação do VAD.
O resultado pode demorar mais do que com `base`; o cliente aguarda até 60 segundos.
Para comparar o reconhecimento com contexto de comandos, configure antes de recriar
o serviço (o padrão vazio permite avaliar o modelo sem esse contexto):

```powershell
$env:WHISPER_INITIAL_PROMPT = "Comandos de computador em português: abra, feche, navegador, bloco de notas."
docker compose up --build -d realtime
```

Compare as mesmas gravações com e sem contexto, incluindo frases livres. Para desativá-lo,
remova a variável e recrie o serviço. Reinicie o aplicativo após alterar opções do host.

Para voltar ao fluxo antigo no PowerShell:

```powershell
$env:TRANSCRIPTION_MODE = "batch"
.venv\Scripts\python.exe main.py
# Voltar ao streaming:
Remove-Item Env:TRANSCRIPTION_MODE
```

No Linux/macOS: `TRANSCRIPTION_MODE=batch python main.py`.
O modo batch mantém WAVs, `RECORD_SECONDS`, upload e consulta a cada 500 ms com limite
de 120 segundos. Ele precisa de Redis e worker Celery, além da API.

### Organização da integração

- `wakeword` possui o único stream do microfone, wake word ONNX e Silero VAD. Envia PCM16
  mono a 16 kHz em blocos de 80 ms; uma thread de transporte aguarda o resultado final.
- `speakbar` recebe eventos pelo controlador Qt. Captura, modelos e rede ficam fora da
  thread gráfica; as ondas animam apenas durante a captura.
- `api` encaminha o WebSocket `/transcribe/stream` ao container `realtime` pela rede interna.
- `worker/realtime.py` carrega uma instância de Whisper **small / CPU / float32 / 4 threads**,
  com português, `beam_size=5` e temperatura zero. O processo é separado do Celery.
- O servidor acumula até 30 segundos de áudio em memória e só inicia a inferência ao
  receber a finalização da captura. Não são geradas transcrições parciais.
- Uploads manuais e GET `/transcribe/{task_id}/status` permanecem disponíveis. O worker
  Celery também usa `small`, temperatura zero e beam 5; a retenção e o formato das respostas continuam iguais.

Protocolo de streaming: [docs/streaming.md](docs/streaming.md).
## Reconhecimento de voz e modelos

O serviço de voz escuta o wake word **"hey jarvis"** no microfone.

O detector usa **ONNX Runtime em CPU** em Windows, Linux e macOS. TFLite não é usado
na inferência e a supressão Speex fica desativada, pois sua integração no openWakeWord
não é portátil entre esses sistemas. Referências: [openWakeWord](https://github.com/dscripka/openWakeWord)
e [ONNX Runtime](https://onnxruntime.ai/docs/get-started/with-python.html).

Use Python **3.11 de 64 bits** como base comum aos três sistemas. No Windows, esta
implementação também foi validada com Python 3.12. No Linux, openWakeWord 0.6.0 declara
`tflite-runtime` como dependência transitiva de instalação, mesmo ao usar ONNX.
Os wheels dessa dependência não cobrem Python 3.12; use uma venv Python 3.11 no Linux.

Na primeira execução, os três modelos ONNX da wake word e o Silero VAD são baixados para
`~/.cache/jarvis/openwakeword-0.6.0` (`~` é a pasta do usuário, também no Windows).
Depois, esses modelos funcionam offline. O Whisper tem seu cache separado no Docker.
Para escolher outro diretório dos modelos ONNX, defina a variável
de ambiente `WAKEWORD_MODELS_DIR`. Downloads interrompidos são descartados e tentados
novamente na próxima execução. O microfone só é aberto após carregar os modelos.

Para baixar e validar os modelos **sem abrir o microfone**, com a venv ativada:

```bash
python -m wakeword.model_loader
```

Na pasta do projeto, ative a venv (`(.venv)` aparece no terminal) e rode:

```bash
python main.py --headless
```

Pare com `Ctrl + C`. Saia da venv com `deactivate`.

Ao detectar a chamada, o serviço captura a fala até a pausa e então solicita a transcrição.
A conexão com o backend é preparada antes de indicar disponibilidade. Os modelos
continuam locais: depois dos downloads iniciais, não é necessário enviar áudio a serviços
externos. `DETECTION_THRESHOLD` define o limiar da wake word (padrão `0.3`).

## Windows

Na raiz do projeto, use a venv existente ou crie uma com `python -m venv .venv`.
No PowerShell, sem precisar ativar scripts:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Permita o acesso ao microfone para aplicativos da área de trabalho nas configurações
de privacidade do Windows. Não é necessário instalar `tflite-runtime` ou Speex.

## macOS

Python 3.11 e [Homebrew](https://brew.sh), na raiz do projeto:

```bash
brew install python@3.11 portaudio
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Se o PyAudio falhar (`portaudio.h` não encontrado):

```bash
export CFLAGS="-I$(brew --prefix portaudio)/include"
export LDFLAGS="-L$(brew --prefix portaudio)/lib"
python -m pip install -r requirements.txt
```

Use Python e Homebrew da mesma arquitetura (Intel ou Apple Silicon). Permita o acesso
ao microfone para o terminal ou IDE nas configurações de privacidade do macOS.

## Linux

Debian/Ubuntu com Python 3.11 disponível nos repositórios (por exemplo, Debian 12):

```bash
sudo apt install python3.11 python3.11-venv python3.11-dev build-essential portaudio19-dev
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Em distribuições com outro Python padrão, instale Python 3.11 e seus headers pelo
gerenciador da distribuição ou por um gerenciador de versões antes de criar a venv.
No Fedora, PortAudio usa `portaudio-devel`; no Arch, `portaudio`.
Use uma sessão desktop com dispositivo de entrada disponível..
