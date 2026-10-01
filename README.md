# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão Social) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

## Speakbar integrada ao reconhecimento de voz

O assistente interpreta pedidos com **IA local** e solicita confirmação falada.
Diga “parar” ou “cancelar” como uma frase isolada para interromper. O botão também
cancela. Solicitações de abertura ou fechamento já entregues ao sistema não são desfeitas.

### Preparar IA local no Windows

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Escolha CPU:
docker compose up --build -d
# Ou NVIDIA (validada com GTX 1060 de 6 GB):
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
docker compose exec ollama ollama pull qwen3:4b-instruct
# Aquecimento opcional, sem executar ações no computador:
docker compose exec ollama ollama run qwen3:4b-instruct "Responda apenas: pronto."
.venv\Scripts\python.exe -m host_agent.interrupt
Invoke-RestMethod http://localhost:8000/commands/health
.venv\Scripts\python.exe main.py
```

Escolha uma das configurações Compose e use a mesma nos próximos `up` para preservar
a opção de GPU. O download do Qwen3 4B Instruct reutiliza o volume `ollama-models`.
O primeiro pedido pode ser mais lento pelo carregamento; o modelo permanece aquecido
por cinco minutos após cada chamada. `docker compose exec ollama ollama ps` mostra
uso de GPU/CPU. O Ollama fica acessível somente pela rede interna do Docker.

A confirmação usa uma voz PT-BR instalada no Windows (Microsoft Maria neste ambiente).
O Vosk pequeno é baixado para `~/.cache/jarvis/vosk-model-small-pt-0.3`. Falhas nesses
componentes impedem ações e preservam transcrição; após corrigir a preparação, reinicie
o host. Reconhecimento e interpretação são locais; pesquisas/sites acessam a internet.

Exemplos depois de “hey jarvis”:

- “Abra o Bloco de Notas”, “abra o explorador de arquivos” ou “abra o Chrome”.
- “Abra o Brave” ou “pesquise receitas no Google usando o Firefox”, se instalados.
- “Abra o YouTube” ou “acesse example.com”.
- “Pesquise acessibilidade no Google” ou “pesquise receitas de pão no YouTube”.
- “Abra o meu editor”, usando o nome de um aplicativo encontrado nos atalhos ou menus.
- “Feche o Word” ou “feche o navegador”, inclusive se foram abertos manualmente.

O assistente fala a proposta e escuta a resposta sem outra wake word. Depois da pergunta,
responda “sim”, “confirmo” ou “pode executar”; “não” cancela. Há até 30 segundos para
responder e uma repetição se inconclusiva. Somente transcrições finais autorizam ações.
Durante a pergunta, confirmações são descartadas para evitar autorização pela própria
voz sintetizada; o monitor de interrupção permanece ativo.

“Solicitação enviada ao sistema” confirma o despacho, não o carregamento da página.
Digitação, cliques, rolagem e sequências gerais ainda não estão disponíveis.

### Descoberta automática e fechamento

O catálogo não depende de uma lista de marcas. Consulta somente as fontes abaixo,
prioriza o desktop e atualiza em segundo plano a cada 60 segundos. Uma busca sem
correspondência provoca atualização adicional. Não varre o disco nem instala aplicativos.

| Sistema | Descoberta | Fechamento normal | Voz |
| --- | --- | --- | --- |
| Windows | Desktop real/público e menus Iniciar pelas pastas conhecidas da Shell; `.lnk` e AppsFolder. Respeita redirecionamento para OneDrive. | `WM_CLOSE` nas janelas identificadas da sessão. | SAPI PT-BR + Vosk 0.3.45. |
| macOS | Bundles em `/Applications`, `~/Applications`, `/System/Applications`; aliases e links no desktop. | `NSRunningApplication.terminate()`. | `say` com voz PT-BR + Vosk 0.3.42. |
| Linux X11 | Desktop XDG e menus via GIO/DesktopAppInfo, incluindo exportações Snap/Flatpak. | `_NET_CLOSE_WINDOW`. | eSpeak NG PT-BR + Vosk 0.3.45. |
| GNOME/Wayland | Mesmo catálogo GIO. | Extensão JARVIS descrita na instalação Linux. | Mesmo TTS e detector do Linux. |
| KDE/Wayland | Abertura via GIO disponível. | Ainda indisponível. | Mesmo TTS e detector do Linux. |

Atalhos equivalentes são reunidos; parâmetros diferentes geram variantes. Nomes
ambíguos exigem esclarecimento. Documentos, pastas, sites, scripts avulsos e entradas
inválidas são excluídos. No Linux, lançadores de desktop precisam estar autorizados
pelo usuário; entradas ocultas ou dependentes de terminal ficam fora deste catálogo.
Aplicativos portáteis precisam de um atalho/lançador nas fontes consultadas.

Navegadores são identificados pelas associações registradas. A escolha explícita tem
prioridade, seguida do padrão encontrado e de um navegador disponível no catálogo.
Não há mais ordem fixa por marcas. “Feche o navegador” pergunta qual quando há vários
em execução. A IA recebe no máximo 20 candidatos, com nomes, aliases e IDs; caminhos,
argumentos e PIDs permanecem no host. Alterar o lançador depois da proposta invalida
a execução e exige um novo pedido.

O fechamento abrange o aplicativo inteiro e suas janelas, inclusive aplicativos
abertos manualmente. Após a confirmação comum, o assistente solicita fechamento normal
e observa por até 15 segundos. Se permanecer aberto, avisa sobre documento não salvo
ou falta de resposta. Uma segunda captura, de até 30 segundos, aceita somente
**“forçar fechamento”** para encerrar os processos confirmados, com risco de perda de
trabalho. “Sim”, silêncio ou cancelamento não autorizam essa etapa. O JARVIS não
clica em Salvar/Descartar. Mudanças nas janelas ou identidade exigem novo pedido.

Não fecha serviços, processos de outro usuário, o próprio JARVIS ou componentes do
desktop. Explorer e gerenciadores de arquivos Linux não podem ser encerrados à força.
O Finder não é anunciado para fechamento: sua integração atual opera sobre o processo
compartilhado do desktop, sem fechamento seguro de janelas individuais. Hosts UWP
compartilhados sem identidade verificável também ficam fora do inventário Windows.
Sem adaptador de fechamento, as aberturas continuam disponíveis.

Diagnóstico local, sem microfone nem Docker: `python -m host_agent.catalog`.
Mostra fontes, variantes, destinos locais, exclusões com motivos, aplicativos em
execução e disponibilidade do fechamento. O diagnóstico contém caminhos locais;
revise-os antes de compartilhar.

**Validação:** testes automatizados portáveis e três testes nativos de janelas no
Windows concluídos. macOS Intel/Apple Silicon, Linux X11, GNOME/Wayland e a matriz
completa Windows 10/11 ainda exigem aceitação em máquinas nativas. A integração de voz
está habilitada nos três sistemas quando as dependências estão prontas; isso não
substitui a validação acústica com microfone e usuário real.

Referências: [GIO AppInfo](https://docs.gtk.org/gio/method.AppInfo.launch.html),
[WM_CLOSE](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-close),
[NSRunningApplication](https://developer.apple.com/documentation/appkit/nsrunningapplication)
e [EWMH/X11](https://specifications.freedesktop.org/wm/latest-single/).

API e host usam o **contrato versão 2**. Atualize ambos; versões incompatíveis geram
mensagem solicitando atualização. Para atualizar apenas a API e preservar os demais
containers: `docker compose up --build --no-deps -d api`. Depois reinicie o host.

Para preservar somente a transcrição anterior:

```powershell
$env:COMMANDS_ENABLED = "0"
.venv\Scripts\python.exe main.py
# Reativar ações na próxima execução:
Remove-Item Env:COMMANDS_ENABLED
```

### Reconhecimento e interface

Após a preparação acima, execute `.venv\Scripts\python.exe main.py` na raiz do projeto.

O Docker Desktop precisa estar em execução antes de iniciar os containers.
A speakbar pode abrir sem Docker, mas a transcrição exige API, Redis e worker disponíveis.

- Aguarde “Aguardando comando” e diga **“hey jarvis”** ou clique no botão de ondas.
- A barra mostra “Ouvindo…” durante a gravação e “Transcrevendo…” durante o envio e a consulta.
- A frase completa aparece quando a transcrição termina e permanece até o próximo comando.
  A wake word volta a ficar ativa enquanto esse resultado continua visível.
- A barra expande para até três linhas. Textos maiores têm rolagem por mouse e teclado.
  Arraste a área central para reposicioná-la; a posição não é salva.
- Clique novamente nas ondas durante gravação ou transcrição para cancelar.
  Se a tarefa já chegou ao servidor, ela pode terminar, mas seu resultado não será mostrado.
- X, Alt+F4 e “Sair” encerram a interface e a captura de áudio. Durante uma operação em
  andamento, a barra mostra “Encerrando…” até liberar os recursos; ela não fica minimizada.
- Tab navega entre voz, fechar e texto; Enter/Espaço ativam os botões.
  Com o texto focado, setas e PageUp/PageDown permitem navegar pela transcrição.
- Falhas de áudio ou modelos permitem nova tentativa pelo botão. Em falhas de transcrição,
  verifique o Docker e tente um novo comando por voz ou pelo botão.

A gravação mantém `RECORD_SECONDS` (5 segundos por padrão). Não há transcrição parcial
enquanto se fala, execução de comandos por IA ou confirmação por voz nesta etapa.
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
basta instalar `speakbar/requirements.txt`; para o terminal com ações, instale o
requirements da raiz. `wakeword/requirements.txt` atende o terminal somente de
transcrição (`COMMANDS_ENABLED=0`).
Não execute simultaneamente duas instâncias reais que disputem o mesmo microfone.

### Organização da integração

- `wakeword` possui o microfone, o modelo ONNX e o cliente HTTP, emitindo eventos sem depender do Qt.
- `speakbar` adapta esses eventos para a interface; o trabalho de áudio e rede roda em uma QThread.
- `api` recebe o upload e disponibiliza o resultado; `worker` continua executando o Whisper.
- O cliente consulta o task_id a cada 500 ms, com limite de 120 segundos. Resultados cancelados
  são descartados pela identificação da interação. As falhas do GET retornam
  `result: null` e `error` textual; o formato de sucesso permanece o mesmo.
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

Na primeira execução, três modelos ONNX oficiais são baixados para
`~/.cache/jarvis/openwakeword-0.6.0` (`~` é a pasta do usuário, também no Windows).
Depois, a detecção funciona offline. Para escolher outro diretório, defina a variável
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

Ao detectar a chamada, o serviço grava o comando (5 segundos por padrão) e o envia
à API de transcrição. Essa etapa precisa dos serviços do `docker-compose.yml` em
execução; a detecção isolada não precisa deles. `API_URL` define o endereço da API
(padrão `http://localhost:8000`), `RECORD_SECONDS` define a duração da gravação e
`DETECTION_THRESHOLD` define o limiar da detecção (padrão `0.3`).

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

Instale uma voz de português do Brasil nas configurações de fala/acessibilidade.
Confira com `say -v '?'`; opcionalmente defina `TTS_VOICE` com o nome exato da voz.
O requirements seleciona Cocoa e Vosk 0.3.42 para macOS (Intel/Apple Silicon).
Prepare Docker/Ollama como na seção inicial e execute `python -m host_agent.interrupt`
para preparar o detector. Falhas de TTS/interrupção deixam somente a transcrição.

## Linux

Debian/Ubuntu com Python 3.11 disponível nos repositórios (por exemplo, Debian 12):

```bash
sudo apt install python3.11 python3.11-venv python3.11-dev build-essential portaudio19-dev pkg-config libcairo2-dev libgirepository1.0-dev gir1.2-glib-2.0 espeak-ng
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Em distribuições com outro Python padrão, instale Python 3.11 e seus headers pelo
gerenciador da distribuição ou por um gerenciador de versões antes de criar a venv.
No Fedora, PortAudio usa `portaudio-devel`; no Arch, `portaudio`.
Use uma sessão desktop com dispositivo de entrada disponível.

### Verificação da integração

```bash
python -m unittest discover -s tests -v
```

Os testes automatizados usam microfone e rede simulados, incluindo cancelamento, falhas,
texto longo e encerramento da thread. No Windows, também foi validado o fluxo com Docker
e reprodução do áudio já gravado no projeto: o texto da API foi comparado ao da speakbar.
A aparência foi conferida em 100%, 125% e 150%. A transcrição de uma nova fala ao vivo e a
execução nativa em Linux/macOS ainda precisam ser verificadas nesses equipamentos.
