# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

O ponto de entrada atual escuta o wake word **"hey jarvis"** no microfone.

Na pasta do projeto, ative a venv (`(.venv)` aparece no terminal) e rode:

```bash
cd src
python main.py
```

Pare com `Ctrl + C`. Saia da venv com `deactivate`.

## Windows

Python 3 no PATH. No PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se a execução de scripts estiver bloqueada: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## macOS

Python 3 e [Homebrew](https://brew.sh):

```bash
brew install portaudio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Se o PyAudio falhar (`portaudio.h` não encontrado):

```bash
export CFLAGS="-I/opt/homebrew/include"
export LDFLAGS="-L/opt/homebrew/lib"
```

## Linux

Debian/Ubuntu:

```bash
sudo apt install python3 python3-venv python3-pip python3-dev portaudio19-dev
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Fedora: `python3-devel` e `portaudio-devel`. Arch: `python` e `portaudio`.
