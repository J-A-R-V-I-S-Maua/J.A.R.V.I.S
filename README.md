# J.A.R.V.I.S

J.A.R.V.I.S. (Janela de Apoio e Reconhecimento Virtual de Inclusão) é uma solução computacional baseada em IA que promove a acessibilidade digital para pessoas com dificuldades motoras. Por meio de comandos de voz, permite controlar o computador, escrever textos, mover o mouse, abrir programas e enviar e-mails.

## Speakbar — interface demonstrativa para Windows

A speakbar é uma barra flutuante independente do agente de voz. Não usa microfone,
rede ou Docker durante a execução. Requer Python 3.10 ou superior (validada com Python 3.12).

Na pasta raiz do projeto, execute no PowerShell:

```powershell
python -m venv .venv  # somente se ainda não existir
.venv\Scripts\python.exe -m pip install -r speakbar/requirements.txt
.venv\Scripts\python.exe -m speakbar
```

Com a venv ativada, também é possível iniciar com `python -m speakbar`.
A instalação das dependências requer internet; a demonstração funciona offline.

- A barra aparece centralizada embaixo, acima da barra de tarefas e das janelas comuns.
- Arraste a região central para reposicioná-la, inclusive entre monitores. A posição não é salva.
- O botão de ondas simula escuta (3 segundos), processamento (2 segundos) e resultado
  (3 segundos), retornando à saudação. Clique novamente para cancelar.
- O texto é somente leitura. Nenhum áudio é capturado ou comando executado.
- X ou Alt+F4 encerram completamente o aplicativo, cancelam a demonstração e removem
  o ícone da bandeja. Para abrir novamente, execute `python -m speakbar`.
- O menu do ícone próximo ao relógio também oferece “Sair” para encerrar o aplicativo.
- Tab alterna entre os botões; Enter ou Espaço ativam o botão focado. O foco tem contorno azul.

O visual usa transparência e gradiente, sem desfoque nativo. A integração com wake word,
transcrição, controle por voz e respostas reais será feita em uma etapa posterior.
Não inclui instalador nem inicialização automática com o Windows.

Para verificar o ciclo e o cancelamento da demonstração:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Agente de voz existente

O ponto de entrada `main.py` escuta o wake word **"hey jarvis"** no microfone.

Na pasta do projeto, ative a venv (`(.venv)` aparece no terminal) e rode:

```bash
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
