# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão) é um projeto de acessibilidade digital para pessoas com dificuldades motoras. Reconhece pedidos, confirma por voz, abre aplicativos descobertos no computador, acessa sites, pesquisa e solicita o fechamento de aplicativos. Há adaptadores para Windows, macOS e Linux; a aceitação nativa concluída nesta fase é a do Windows. Digitação, controle do mouse e e-mails permanecem como etapas futuras.

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
A transcrição em streaming exige a API e o novo serviço `realtime` disponíveis.
Na primeira inicialização, o `realtime` baixa o Whisper `base` para o volume de cache
e aquece o modelo antes de aceitar conexões. Acompanhe com `docker compose logs -f realtime`;
`Application startup complete` indica que está pronto. Os próximos inícios reutilizam o cache.
A demonstração `--demo` continua funcionando sem Docker.

- Aguarde “Aguardando comando” e diga **“hey jarvis”** ou clique no botão de ondas.
- A barra mostra “Ouvindo…” e começa a exibir transcrições parciais enquanto você fala.
  Cada hipótese substitui a anterior: palavras podem ser corrigidas durante a fala.
- Após aproximadamente **2 segundos de silêncio**, a captura termina. A última parcial
  permanece visível enquanto o servidor finaliza; depois é substituída pelo resultado final.
  Sem fala, a interação termina após **10 segundos**; cada comando dura no máximo **30 segundos**.
- Com ações habilitadas, o texto final segue para a IA e a barra mostra interpretação,
  pergunta, confirmação e resultado. No modo somente transcrição, a frase final permanece.
  A wake word volta ao término da interação.
- A barra expande para até três linhas. Textos maiores têm rolagem por mouse e teclado.
  Arraste a área central para reposicioná-la; a posição não é salva.
- Clique novamente nas ondas durante captura ou transcrição para cancelar.
  Uma inferência já iniciada pode terminar no servidor, mas seu resultado será descartado.
- X, Alt+F4 e “Sair” encerram a interface e a captura de áudio. Durante uma operação em
  andamento, a barra mostra “Encerrando…” até liberar os recursos; ela não fica minimizada.
- Tab navega entre voz, fechar e texto; Enter/Espaço ativam os botões.
  Com o texto focado, setas e PageUp/PageDown permitem navegar pela transcrição.
- Falhas de áudio, modelos ou conexão permitem nova tentativa pelo botão.
  Em caso de desconexão, uma parcial nunca é tratada como resultado final.
  Uma interação sem fala volta à escuta da wake word automaticamente.

No modo padrão, o áudio fica **somente em memória**, sem novos WAVs em `recordings/`
ou uploads gravados no servidor. As gravações anteriores são preservadas.
Nos sistemas suportados, ações e confirmação por voz ficam habilitadas por padrão;
`COMMANDS_ENABLED=0` preserva o reconhecimento isolado.
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
O serviço `realtime` atende uma conexão de reconhecimento por vez.

### Configuração e modo anterior

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `TRANSCRIPTION_MODE` | `stream` | `stream` usa parciais; `batch` usa gravação e upload antigos. |
| `STREAM_SILENCE_SECONDS` | `2` | Silêncio necessário para finalizar depois de detectar fala. |
| `STREAM_START_TIMEOUT_SECONDS` | `10` | Espera máxima pela primeira fala. |
| `STREAM_MAX_SECONDS` | `30` | Duração máxima da interação, incluindo espera inicial. |
| `API_URL` | `http://localhost:8000` | Endereço público da API; o cliente deriva a URL WebSocket. |
| `RECORD_SECONDS` | `5` | Duração da gravação, **somente no modo batch**. |
| `COMMANDS_ENABLED` | `1` | Ações nos sistemas suportados quando as dependências estão prontas; `0` preserva somente transcrição. |
| `TTS_VOICE` | primeira voz PT-BR encontrada | Seleção opcional de voz instalada para `say` no macOS. |
| `OLLAMA_MODEL` | `qwen3:4b-instruct` | Modelo do backend; definir antes de recriar a API e baixar o mesmo modelo. |
| `INTERRUPT_MODEL_DIR` | cache do usuário | Caminho de um modelo Vosk PT já extraído no host. |

Os três tempos de streaming aceitam números positivos até 30 segundos. Para pausas
maiores durante a fala, aumente `STREAM_SILENCE_SECONDS`, respeitando a duração máxima.
Defina as variáveis antes de iniciar o aplicativo. Exemplo no PowerShell:

```powershell
$env:STREAM_SILENCE_SECONDS = "3"
.venv\Scripts\python.exe main.py
```

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

- Com ações habilitadas, `host_agent` possui o único stream do microfone, distribui
  áudio para wake word ONNX, Silero VAD e interrupção Vosk. `wakeword` preserva o modo
  independente de transcrição. O reconhecimento recebe PCM16
  mono a 16 kHz em blocos de 80 ms; uma thread de transporte recebe parciais simultaneamente.
- `speakbar` recebe eventos pelo controlador Qt. Captura, modelos e rede ficam fora da
  thread gráfica; as ondas animam apenas durante a captura.
- `api` encaminha o WebSocket `/transcribe/stream` ao container `realtime` pela rede interna.
- `worker/realtime.py` carrega uma instância de Whisper **base / CPU / INT8 / 4 threads**,
  com português, `beam_size=1` e temperatura zero. O processo é separado do Celery.
- A cada segundo de áudio adicional, o servidor reprocessa o contexto acumulado. Há no
  máximo uma inferência ativa e uma versão mais recente pendente, limitada a 30 segundos.
- Uploads manuais e GET `/transcribe/{task_id}/status` permanecem disponíveis. O modelo
  Whisper do worker Celery, a retenção de uploads e os resultados desses endpoints não mudaram.

Arquitetura atual: [ResumoArquitetura.md](ResumoArquitetura.md).

### Testes

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

Por padrão, os testes não abrem aplicativos: verificam contratos, catálogo,
confirmação, cancelamento, fechamento e controle de áudio. Para os três testes nativos
Windows, que criam e encerram exclusivamente janelas descartáveis da própria suíte:

```powershell
$env:JARVIS_NATIVE_TESTS = "1"
.venv\Scripts\python.exe -m pytest -q tests/test_native_windows.py
Remove-Item Env:JARVIS_NATIVE_TESTS
```

Os testes permanentes ficam em `tests/`, incluindo a fixture de janela e diálogo de
documento não salvo. Resultados medidos estão em [HistoricoEvolucao.md](HistoricoEvolucao.md).
A validação com fala humana e microfone continua necessária. Em cada novo ambiente,
teste um aplicativo fora do antigo catálogo, fechamento de um aplicativo aberto
manualmente, documento descartável não salvo, cancelamento durante fala/espera e a
segunda confirmação para forçar. Registre descoberta, interpretação e interrupção;
não use documentos de trabalho nesses testes.

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

Ao detectar a chamada, o serviço inicia a captura contínua com parciais até a pausa.
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

PyGObject/GIO realiza a descoberta e o lançamento; python-xlib atende X11.
Confira a voz com `espeak-ng -v pt-br "Teste de voz"`. Prepare Docker/Ollama como na
seção inicial e execute `python -m host_agent.interrupt` para preparar o Vosk.
Para somente transcrição: `COMMANDS_ENABLED=0 python main.py` (também no macOS).

No **GNOME/Wayland**, instale a extensão incluída no projeto:

```bash
mkdir -p ~/.local/share/gnome-shell/extensions
cp -R integrations/gnome/jarvis-window-control@jarvis.local ~/.local/share/gnome-shell/extensions/
# Na primeira instalação, saia da sessão e entre novamente para o GNOME descobri-la.
gnome-extensions enable jarvis-window-control@jarvis.local
gnome-extensions info jarvis-window-control@jarvis.local
```

A extensão declara GNOME Shell 46–50; essa matriz ainda não foi validada nativamente.
Ela expõe somente consulta e fechamento de janelas via D-Bus, sem comandos de shell
nem execução de código fornecido pelo host. Sem a extensão ativa, o diagnóstico
explica a indisponibilidade do fechamento. KDE/Wayland, controle de abas, UI Automation,
digitação/cliques/rolagem gerais e respostas automáticas a diálogos ficam para depois.
