# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão Social) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

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
- A frase final permanece até o próximo comando.
  A wake word volta a ficar ativa enquanto esse resultado continua visível.
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
Não há execução de comandos por IA ou confirmação por voz nesta etapa.
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

### Configuração e modo anterior

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `TRANSCRIPTION_MODE` | `stream` | `stream` usa parciais; `batch` usa gravação e upload antigos. |
| `STREAM_SILENCE_SECONDS` | `2` | Silêncio necessário para finalizar depois de detectar fala. |
| `STREAM_START_TIMEOUT_SECONDS` | `10` | Espera máxima pela primeira fala. |
| `STREAM_MAX_SECONDS` | `30` | Duração máxima da interação, incluindo espera inicial. |
| `API_URL` | `http://localhost:8000` | Endereço público da API; o cliente deriva a URL WebSocket. |
| `RECORD_SECONDS` | `5` | Duração da gravação, **somente no modo batch**. |

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

- `wakeword` possui o único stream do microfone, wake word ONNX e Silero VAD. Envia PCM16
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
Use uma sessão desktop com dispositivo de entrada disponível.
