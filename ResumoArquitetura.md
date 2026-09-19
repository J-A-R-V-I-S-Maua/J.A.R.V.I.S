# Agente de Voz para Acessibilidade Motora

Arquitetura do J.A.R.V.I.S., atualizada em 19 de setembro de 2026.

## Objetivo e primeira entrega

Permitir que pessoas com dificuldade motora parcial ou total controlem o computador
por linguagem natural em PT-BR. A entrega abre navegadores do catálogo instalado,
Explorer e Bloco de Notas, acessa endereços e pesquisa no Google/YouTube.

O ciclo é: ativação → transcrição final → interpretação local → confirmação falada →
resposta final explícita → despacho ao Windows. Parciais servem somente ao feedback.

## Divisão entre host e Docker

| Componente | Local | Responsabilidade |
| --- | --- | --- |
| wakeword | Host | Wake word ONNX, VAD, transporte STT e modo somente transcrição. |
| host_agent | Host Windows | Sessão, áudio contínuo, interrupção, confirmação, SAPI e executor. |
| host_agent.catalog / executor | Host Windows, Linux ou macOS | Descoberta local e despacho nativo de navegadores; integração por voz ainda restrita ao Windows. |
| speakbar | Host | Apresentação Qt; captura, rede e TTS fora da thread gráfica. |
| contracts | Host e API | Tipos Pydantic e validação comum das ações. |
| api | Docker | Entrada HTTP/WebSocket, proxy STT e interpretação de comandos. |
| realtime | Docker | Whisper base, CPU INT8, uma conexão por vez. |
| ollama | Docker | Qwen3 4B Instruct Q4_K_M, contexto 4096, processamento local. |
| Redis, worker e beat | Docker | Transcrição batch e retenção de uploads preservadas. |

Ollama deixa de ser uma fase futura. A confirmação sonora e a sessão passam ao host,
junto do executor e da interrupção. A API não tem acesso ao desktop. Não há fallback
para nuvem. O STT, originalmente planejado no host, já roda no Docker desde o streaming.

```text
Microfone no Windows ──┬── Wake word/VAD → API → Whisper
                      └── Vosk → interrupção do coordenador
Texto final → coordenador → API → Ollama → proposta validada
Proposta → pergunta SAPI → confirmação pelo Whisper → executor Windows
                       ↘ estados e texto na speakbar
```

## Contratos e autorização

- POST /commands/interpret recebe interaction_id, text e context: idioma, aplicativos,
  capacidades e eventual esclarecimento pendente.
- Resposta: interaction_id, status (action, clarification ou unsupported), action
  tipada ou nula e message em PT-BR. A API converte a saída estruturada plana do modelo
  para a união de ações compartilhada com o host.
- Esclarecimentos são apresentados ao modelo como diálogo: pedido original, pergunta
  do assistente e resposta, mantendo o destino e o navegador do pedido.
- Ações: open_app(app), open_url(url, browser), search_web(query, provider, browser).
  Não existe operação de shell ou código livre.
- GET /commands/health informa disponibilidade do Ollama e presença do modelo,
  sem bloquear a transcrição.
- O host valida novamente a proposta, constrói a pergunta usando seus parâmetros e
  só executa após “sim”, “confirmo” ou “pode executar” como resposta final.
- Google e YouTube são aliases conhecidos. Outros destinos exigem domínio/URL
  fornecido; somente HTTP/HTTPS sem credenciais. Consultas são codificadas na URL.
- Catálogo: Chrome, Edge, Brave, Firefox, Chromium, Opera e Vivaldi; Safari no macOS.
  O padrão do sistema tem prioridade quando reconhecido e instalado; as ordens de
  alternativa por SO constam no README. Escolhas explícitas restringem o schema da
  IA e são revalidadas no host. Navegador solicitado ausente impede a execução.
  Caminhos, IDs Flatpak e argumentos vêm exclusivamente do catálogo local.
- Descoberta separada por SO em `host_agent/catalog.py`: registro e diretórios no
  Windows; PATH, Snap, Flatpak e XDG no Linux; NSWorkspace e bundles no macOS.
  `NativeExecutor` recebe argumentos fixos do catálogo e acrescenta apenas a URL
  validada. `WindowsExecutor` permanece como interface de compatibilidade.
- Uma ação lógica por interação. Pedidos com escrita, cliques ou múltiplas ações
  independentes devem ser recusados por inteiro.

## Sessão, áudio e falhas

O coordenador mantém uma interação por vez, com identificadores separados para
interação e captura. Uma thread possui o microfone e distribui PCM16 mono de 16 kHz
em blocos de 80 ms. Filas são limitadas; perder áudio durante captura gera erro.

Vosk reconhece interrupções: “parar”, “pare”, “cancelar”, “parar agora” e “cancelar
comando”. A forma reconhecida “para” também é aceita. O resultado precisa ser uma
frase final isolada: consultas como “pesquise como parar de fumar” não devem cancelar.
Vosk não substitui o Whisper para pedidos e confirmações nem autoriza ações.

A próxima captura é preparada antes da pergunta. Durante o TTS não são aceitos novos
pedidos nem confirmações; os buffers são descartados após a fala e uma curta cauda
acústica. O detector de interrupção continua ativo durante processamento e TTS.

A confirmação tem limite de 30 segundos, incluindo transcrição, com uma repetição
se inconclusiva. Informações faltantes permitem um esclarecimento por voz. Sessão e
autorização ficam em memória e não são recuperadas depois de reiniciar o aplicativo.

Cancelamento interrompe TTS, invalida respostas pendentes e impede o próximo despacho.
Uma abertura já entregue ao Windows não é desfeita. Reconhecimento tem latência e
depende da qualidade acústica; não há garantia de tempo real rígido.

Falhas no TTS ou no detector impedem ações e preservam transcrição. Falhas no Ollama
permitem novo pedido por wake word ou botão. Falha no microfone exige recuperar o
dispositivo e tentar novamente. X/Alt+F4 encerra o host e libera seus recursos;
containers permanecem serviços independentes.

## Compatibilidade e evolução

COMMANDS_ENABLED=0 mantém somente transcrição. --demo funciona sem microfone, modelos
ou Docker. Streaming continua padrão; batch mantém WAV, uploads e Celery. Ações são
habilitadas por voz no Windows 10/11; outros sistemas mantêm o reconhecimento.
O catálogo e o executor de navegadores já têm adaptadores Linux/macOS, testados com
ambientes simulados. TTS e aceitação nativa nesses sistemas permanecem pendentes.

Permanecem para depois: digitação, cliques, rolagem, fechamento de janelas, UI Automation,
visão computacional, sequências gerais, macros, memória persistente, outros idiomas,
Claude/nuvem e empacotamento. O MVP mais amplo continua como direção futura.

Testes automatizados de regressão acompanham o código. O roteiro temporário de
validação com áudio sintético foi removido após a conclusão desta fase.
A aceitação com fala real de usuários com dificuldade motora continua necessária.
Os resultados efetivamente medidos constam no histórico de evolução.
