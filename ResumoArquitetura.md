# Agente de Voz para Acessibilidade Motora

Arquitetura do J.A.R.V.I.S., atualizada em 20 de setembro de 2026.

## Objetivo e fluxo

Permitir controle do computador em PT-BR por voz. A implementação descobre aplicativos
registrados, abre aplicativos/sites/pesquisas e fecha aplicativos gráficos identificados,
incluindo os abertos manualmente. Windows, macOS e Linux têm adaptadores; a validação
nativa desta fase foi feita no Windows. A aceitação dos demais ambientes está pendente.

Ativação → transcrição final → interpretação local → confirmação falada → resposta final
explícita → execução nativa. Parciais servem somente ao feedback. O host autoriza ações;
a IA não recebe acesso ao desktop, executáveis, comandos, processos ou shell.

## Divisão entre host e Docker

| Componente | Local | Responsabilidade |
| --- | --- | --- |
| wakeword | Host | ONNX, VAD, transporte STT e modo somente transcrição. |
| host_agent.service | Host | Coordenador independente de Qt, sessão em memória, confirmação e cancelamento. |
| host_agent.speech / interrupt / tts | Host | Microfone único, Whisper, interrupção Vosk e fala cancelável. |
| host_agent.catalog / platform_apps | Host | Descoberta, atualização, seleção de candidatos e abertura nativa. |
| host_agent.running / window_backends | Host | Inventário gráfico, identidade, fechamento normal e forçado. |
| integrations/gnome | GNOME Shell | Extensão limitada a consulta e fechamento de janelas via D-Bus. |
| speakbar | Host | Apresentação Qt de escuta, interpretação, fala, confirmação e execução. |
| contracts | Host e API | Modelos Pydantic v2 e validação compartilhada das propostas. |
| api | Docker | HTTP/WebSocket, proxy STT e interpretação direta, fora do Celery. |
| realtime | Docker | Whisper base, CPU INT8, quatro threads, uma conexão por vez. |
| ollama | Docker | Qwen3 4B Instruct Q4_K_M, contexto 4096, temperatura zero. |
| Redis, worker e beat | Docker | Transcrição batch e retenção de uploads preservadas. |

O microfone distribui PCM16 mono a 16 kHz em blocos de 80 ms para wake word/VAD,
transporte Whisper e Vosk. Captura, rede, interpretação e TTS ficam fora da thread Qt.
Ollama tem volume persistente, configurações CPU/NVIDIA e limite de interpretação
de 30 segundos. Não há dependência do Ollama para transcrever nem fallback para nuvem.

## Catálogo dinâmico e contexto

Fontes delimitadas, sem varredura indiscriminada:

- Windows: pastas conhecidas da Shell para desktop real/público e menus Iniciar,
  resolução de atalhos .lnk, AppsFolder e associações HTTPS registradas.
- Linux: desktop XDG e menus via GIO/DesktopAppInfo, respeitando exportações Snap/Flatpak.
  Abertura delegada ao GIO, sem reconstruir Exec, códigos de campo ou ativação D-Bus.
- macOS: aliases/links do desktop e bundles em /Applications, ~/Applications e
  /System/Applications; validação de bundle, plist e executável interno.

O catálogo registra ID estável, nomes, aliases, fontes, identidade nativa, lançador e
impressões dos arquivos. Os dados privados de lançamento ficam somente no host.
Documentos, pastas, sites, scripts avulsos, entradas inválidas e aplicativos auxiliares
não são tratados como aplicativos gráficos. Exclusões têm motivo no diagnóstico.
Entradas Linux ocultas ou que exigem terminal são excluídas; lançadores do desktop
precisam estar autorizados pelo usuário.

Deduplicação usa identidade e configuração de lançamento; parâmetros diferentes
preservam variantes. Desktop tem preferência secundária ao nome/aliases. Nomes
ambíguos pedem esclarecimento. Inicialização e atualização periódica a cada 60 segundos;
busca sem correspondência atualiza antes de declarar ausência. Sem histórico de uso.

Seleção local limita o contexto a 20 candidatos, separando disponíveis para abertura
e aplicativos em execução para fechamento. O modelo recebe IDs, nomes e aliases,
tratados como dados não confiáveis. Não recebe PIDs, caminhos ou argumentos.
O navegador explícito tem prioridade, seguido da associação padrão descoberta e de
outro navegador disponível. Nomes de marcas são aliases, não uma lista de instalações.

## Contrato versão 2

POST /commands/interpret exige protocol_version=2, interaction_id, text e context.
A resposta inclui a mesma versão/identificador e status action, clarification ou
unsupported, com ação tipada ou mensagem. Incompatibilidade retorna HTTP 409 e pede
atualização conjunta de API e host; dados inválidos retornam 422.

Ações: open_app(app), open_url(url, browser), search_web(query, provider, browser) e
close_app(target_id). Os campos app/browser/target_id referem-se aos IDs candidatos.
Não existe ação pública para forçar encerramento nem campo para comandos livres.

Pedidos simples com correspondência inequívoca podem ser resolvidos deterministicamente.
Demais pedidos usam saída estruturada Ollama com schema limitado aos IDs candidatos.
API e host validam novamente. Resposta inválida, timeout ou falha não autorizam ação.
Metadados do catálogo não são instruções. O host constrói a confirmação a partir da
ação validada e revalida lançador/identidade imediatamente antes do despacho.

GET /commands/health informa versão, disponibilidade do interpretador e modelo.
Esclarecimento preserva pedido original, pergunta e resposta em um diálogo limitado.
Google/YouTube são aliases de sites; demais URLs devem ser fornecidas pelo usuário,
somente HTTP/HTTPS sem credenciais. Consultas são codificadas na URL.
Uma ação lógica por interação; operações fora do escopo não devem ser executadas parcialmente.

## Identidade e fechamento

O inventário enumera aplicativos gráficos da sessão do usuário sem depender de quem
os abriu. Agrupa janelas por identidade nativa, associa processos a usuário, PID e
horário de criação e exclui JARVIS, serviços e componentes protegidos da sessão.
Intérpretes compartilhados não são agrupados somente pelo nome do executável.
Processos filhos só são incluídos quando a identidade pode ser comprovada.

| Sistema | Solicitação normal | Forçado |
| --- | --- | --- |
| Windows | WM_CLOSE nas janelas confirmadas. | psutil com PID, criação, usuário e executável revalidados. |
| macOS | NSRunningApplication.terminate(). | forceTerminate() com identidade e data de lançamento verificadas. |
| Linux X11 | _NET_CLOSE_WINDOW para janelas locais verificadas. | Processos locais confirmados e revalidados. |
| GNOME/Wayland | Extensão JARVIS lista janelas e solicita window.delete(). | Processos confirmados e revalidados pelo host. |

Primeira confirmação informa que todas as janelas serão abrangidas. Antes do despacho,
mudanças no conjunto ou identidade exigem um novo pedido. Após fechamento normal,
observa por até 15 segundos fora do bloqueio da interface. Se continuar aberto, preserva
o conjunto de processos e atualiza as janelas para incluir eventual diálogo de salvar.

A segunda pergunta avisa sobre perda de trabalho e abre nova captura de até 30 segundos.
Somente a frase final “forçar fechamento” autoriza força. “Sim”, silêncio, resposta
inconclusiva ou cancelamento preservam o aplicativo. Nenhum botão Salvar/Descartar é
acionado. Antes de forçar, revalida novamente o conjunto e cada processo; verifica o
resultado após o despacho. Fechar a janela e permanecer na bandeja pode levar à
segunda pergunta, pois o pedido abrange o aplicativo inteiro.

Gerenciadores de arquivos não podem ser encerrados à força. O Finder fica indisponível
para fechamento até existir integração segura de suas janelas; terminate() afetaria o
desktop compartilhado. Hosts UWP compartilhados sem identidade verificável são excluídos.
Sem extensão GNOME ou adaptador verificável, desabilita fechamento e preserva abertura.
KDE/Wayland não tem adaptador de fechamento. Não existe fallback por teclas globais.

## Sessão, confirmação e falhas

Sessão e autorização ficam em memória, com IDs separados para interação e captura.
Resultados atrasados são descartados; cada ação confirmada pode ser despachada uma vez.
A confirmação comum aceita “sim”, “confirmo” ou “pode executar”; “não” cancela.
Limite de 30 segundos com uma repetição inconclusiva; no máximo um esclarecimento.

TTS: SAPI em thread própria no Windows; processo exclusivo say no macOS; eSpeak NG
em PT-BR no Linux. Textos são dados, sem shell. Vosk pequeno PT reconhece exclusivamente
interrupções isoladas (“parar”, “cancelar” e variantes), inclusive durante TTS ou
indisponibilidade da IA. Consultas contendo essas palavras não são interrupções isoladas.
Vosk 0.3.45 no Windows/Linux e 0.3.42 no macOS.

Durante TTS bloqueia novas autorizações; após a fala e cauda acústica, descarta buffers
antes da captura automática. Cancelamento interrompe TTS, invalida respostas pendentes
e impede próximos despachos. Não desfaz pedidos já entregues ao sistema operacional.
A espera por fechamento não bloqueia captura, cancelamento ou interface.

Falhas de TTS/interrupção preservam reconhecimento e impedem ações. Falha de integração
de fechamento não impede aberturas. COMMANDS_ENABLED=0 mantém somente transcrição;
--demo dispensa microfone/modelos/Docker. Streaming e batch/Celery permanecem disponíveis.
Encerramento cooperativo libera áudio, TTS, transporte e atualização do catálogo.

## Validação e próximos passos

Testes permanentes cobrem catálogo, variantes, limites, contratos, confirmação, negação,
silêncio, esclarecimento, cancelamento, alterações de identidade, PID reutilizado,
segunda autorização, resultados atrasados, execução duplicada e regressões de áudio.
Testes nativos Windows optativos criam exclusivamente janelas descartáveis da suíte.

Resultados medidos estão no HistoricoEvolucao.md. Ainda são necessários testes nativos
macOS Intel/Apple Silicon, Linux X11, GNOME/Wayland e matriz completa Windows 10/11,
além da aceitação acústica por usuários reais. Instalação, diagnóstico e preparação
da extensão GNOME estão no README.

Fora desta etapa: abas individuais, digitação/cliques/rolagem gerais, UI Automation,
respostas automáticas a diálogos de salvamento, fechamento de serviços, KDE/Wayland,
visão computacional, sequências gerais, macros, memória persistente e nuvem.
