# Arquitetura atual do J.A.R.V.I.S.

O aplicativo auxilia pessoas com dificuldades motoras a controlar o computador por
voz, em português brasileiro, no Windows, no macOS e no Linux, com feedback
somente textual. Este documento descreve o que está implementado; a evolução
planejada aparece ao final.

## Fluxo implementado

1. A wake word “Hey Jarvis” ou o botão inicia a captura no host.
2. O VAD detecta fala e encerra após o silêncio configurado.
3. No modo padrão, PCM é enviado por WebSocket; o Whisper transcreve o áudio
   completo uma vez, após o fim da fala.
4. O coordenador escolhe o contexto: diagnóstico, configurações, sessão pendente,
   ajuda, atalhos ou comandos de tela. O texto ditado permanece literal.
5. A interface separa estado operacional de mensagem e orientação. Silêncio,
   transcrição vazia e frase desconhecida recebem feedback e uma nova captura.
6. Cliques, digitação, Enter, limpeza, recortar e colar exigem confirmação e revalidam
   seus alvos. Rolagem, atalhos, selecionar, copiar, comandos de navegador e a escolha de
   uma janela no Mission Control executam diretamente.
7. O resultado permanece visível. No modo contínuo, um comando concluído abre uma escuta
   curta para o próximo, sem wake word; silêncio volta ao "Hey Jarvis" sem erro.

## Componentes e responsabilidades

| Componente | Local | Responsabilidade |
|---|---|---|
| `wakeword` | Host | Microfone, wake word, VAD, transporte e eventos tipados |
| `speakbar.interaction` | Host | Contexto de diálogo, roteamento e repetição de comandos desconhecidos |
| `speakbar.commands` | Host | Vocabulário da barra, ajuda e números dos ajustes (normaliza com `matching`) |
| Controladores Qt | Host | Ponte entre serviços, sessão e apresentação; agendamento de capturas |
| Speakbar e configurações | Host | Estado e orientação visíveis; edição de rascunho e persistência explícita |
| `screen_access` | Host | Sessão de escolha/confirmação, acompanhamento de foco e ações nativas |
| `screen_access.windows` | Host | UI Automation e o Win32 de apoio (foco, área de trabalho, digitação) |
| `screen_access.macos*` | Host | AX API (`macos`), mesa/Dock/Mission Control/menus (`macos_shell`) e o backend |
| `screen_access.linux` | Host | AT-SPI; teclas por xdotool/ydotool (`keys`) e números por layer-shell (`overlay`) |
| `screen_access.matching` | Host | Normalização de texto comum a todo o projeto e escolha por semelhança sonora |
| API FastAPI | Docker | Proxy WebSocket e endpoints legados de transcrição em lote |
| `realtime` | Docker | Whisper small, aquecimento e uma conexão de reconhecimento por vez |
| Worker, Beat e Redis | Docker | Modo em lote (opcional, perfil `batch`): processamento e limpeza dos uploads |

As dependências do host ficam todas no `requirements.txt` da raiz, com marcadores por sistema.

A correspondência de nomes de controles mantém seu tratamento fonético próprio.
Os módulos de diálogo não dependem de widgets. As referências nativas COM/AX e
as operações sobre elas pertencem à thread do executor; áudio pertence à thread
do serviço de voz. A thread gráfica não captura áudio nem executa inferência.

## Estados, recuperação e proteção contra respostas atrasadas

Eventos de voz mantêm identificador de interação, resultado final, motivo tipado
quando aplicável e disponibilidade da captura. Motivos distinguem ausência de
fala, transcrição vazia, conexão, microfone e preparação de modelos indisponíveis.
O modelo de apresentação tem estado, mensagem, orientação e disponibilidade.

Cada etapa de escolha, confirmação ou ditado admite uma repetição no prazo
original de 30 segundos. Ajuda não renova o prazo. Atalhos têm o mesmo limite
para resolver sua frase. A espera por execução nativa tem prazo de 90 segundos.
Troca de janela, mudança do alvo, cancelamento e expiração invalidam propostas.
Ações enviadas não são repetidas automaticamente.

Solicitações de captura verificam prontidão e aceitação do serviço. A geração do
diálogo, o serviço de origem e o token da sessão impedem capturas obsoletas.
Eventos de interações canceladas são descartados. Eventos válidos da mesma
interação são processados em ordem, incluindo o resultado final seguido de
prontidão, sem depender de o evento ainda ser o snapshot mais recente.

A recuperação de conexão espera 1, 2, 5, 10 e 15 segundos, mantendo 15 segundos
nas falhas seguintes até uma interação bem-sucedida. O microfone selecionado é
reaberto a cada 5 segundos, sem substituição automática por outro dispositivo.
Preparação de modelos com falha também é repetida a cada 5 segundos.
O encerramento interrompe as esperas. Os logs adicionais registram estados,
motivos, identificadores e durações, sem texto reconhecido ou ditado.

## Configurações e dados

A rolagem vertical utiliza `ScrollPattern` no Windows e a barra de rolagem AX no macOS (com a
roda do mouse contínua como alternativa no Chrome e em apps Electron), preservando o alvo na thread
do executor. A região rolável mais próxima do foco é priorizada; ambiguidades são
resolvidas por números. Os movimentos pequeno, normal e página usam aproximadamente
15%, 50% e 90% do viewport, ou o incremento nativo quando essas métricas não existem.
Não há fallback por mouse/teclado nem repetição automática de ações.

Quando existem métricas de posição e viewport, `SetScrollPercent` recebe posições
intermediárias com interpolação suave a até 30 passos por segundo, durante 0,45 s,
0,75 s ou 1,1 s conforme a intensidade. A distância final é preservada e cada passo
revalida janela e área, permitindo interromper a animação. Todo o trabalho permanece
na thread do executor. Provedores que só oferecem incrementos nativos mantêm esse
comportamento, sem simular uma precisão que não disponibilizam.

Após cada movimento, uma captura com espera inicial de cinco segundos aceita
“mais”, troca de direção, nova intensidade ou cancelamento. Silêncio encerra
normalmente. O tempo é específico da captura e não altera preferências globais.
Números de controles visíveis são descartados e relidos após rolar. A referência
de rolagem é separada do snapshot desses controles. Mudança de janela encerra a
sessão; cada movimento revalida área, janela, identidade, geometria e disponibilidade.

Com o menu aberto, comandos externos ficam bloqueados. A voz permite navegar
entre seções, consultar ajuda, ajustar aparência e tempos, salvar ou descartar.
Microfone e atalhos continuam selecionados/cadastrados no formulário. Testes de
transcrição são isolados: nenhuma frase do diagnóstico executa comandos.

Preferências e atalhos usam o arquivo local versionado `settings.json`, com
gravação atômica e preservação de arquivos inválidos. Aparência é aplicada ao
salvar; mudanças de áudio reiniciam o serviço após liberar o dispositivo.

No streaming, o áudio permanece em memória; não são criados WAVs nem uploads
persistentes. No modo legado `batch`, o host cria WAVs em `recordings/` e a API
salva uploads no volume compartilhado. A limpeza periódica do servidor usa a
retenção configurada (padrão de 24 horas); os WAVs normais do host não têm essa
limpeza automática. O diagnóstico em lote remove seu WAV temporário. Resultados
de tarefas em lote também passam pelo backend Celery/Redis.
O modo headless continua voltado à transcrição no terminal, usando o ambiente.

## Limitações e evolução futura

- O controle nativo usa UI Automation no Windows, a AX API no macOS e AT-SPI no Linux.
  No macOS, validado num Mac real com o Tahoe; as versões anteriores são cobertas por testes
  com a árvore medida nelas (o Mission Control era desenhado pelo Dock e no Tahoe passou ao
  WindowManager, e o código procura nos dois). Mission Control, seleção de texto,
  copiar/recortar/colar, comandos de navegador, menu de contexto com opções numeradas e
  "abrir [aplicativo]" pelo nome existem só no macOS por enquanto.
- Aplicativos sem controles acessíveis ou com privilégios diferentes podem recusar
  operações. Não há fallback por coordenadas ou visão computacional.
- “Parar” por voz depende da captura e transcrição. O botão cancela a interação
  local imediatamente; uma ação nativa já enviada pode terminar depois disso.
- Criação completa de atalhos e seleção de microfone por voz ficam para outra etapa.
- LLM, comandos compostos, rolagem horizontal, envio guiado de e-mails, TTS e
  reconhecimento independente de emergência não fazem parte desta entrega.

## Validação

A suíte `python -m unittest discover -s tests -v` cobre contratos de voz, sessão,
roteamento, recuperação, apresentação e configurações, com dublês para Windows, macOS
(versões antigas e Tahoe) e Linux. Ela passa inteira no macOS; no Windows e no Linux ainda
precisa ser rodada nas máquinas desses sistemas. Os scripts explícitos
`screen_native_check.py` e `settings_native_check.py` verificam integração nativa
no Windows. `scroll_native_check.py` verifica rolagem em duas áreas Win32 isoladas.
As prévias `interaction_visual_check.py` e `settings_visual_check.py`
renderizam a interface em 100%, 125% e 150%, sem Whisper ou ações externas.
O teste nativo de configurações usa áudio real e comandos simulados, sem Whisper.
Reconhecimento com fala real deve ser validado separadamente nos aplicativos-alvo.
