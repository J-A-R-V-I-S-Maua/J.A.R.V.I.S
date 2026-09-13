# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

## Speakbar integrada ao reconhecimento de voz

Na raiz do projeto, instale as dependências do host (interface e reconhecimento).
No PowerShell, usando a venv existente:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up --build -d
.venv\Scripts\python.exe main.py
```

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
basta instalar `speakbar/requirements.txt`; para apenas o terminal, basta
`wakeword/requirements.txt`. O requirements da raiz referencia ambos, sem duplicá-los.
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

### Verificação da integração

```bash
python -m unittest discover -s tests -v
```

Os testes automatizados usam microfone e rede simulados, incluindo cancelamento, falhas,
texto longo e encerramento da thread. No Windows, também foi validado o fluxo com Docker
e reprodução do áudio já gravado no projeto: o texto da API foi comparado ao da speakbar.
A aparência foi conferida em 100%, 125% e 150%. A transcrição de uma nova fala ao vivo e a
execução nativa em Linux/macOS ainda precisam ser verificadas nesses equipamentos.
