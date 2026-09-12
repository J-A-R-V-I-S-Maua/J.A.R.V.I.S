# Agente de Voz para Acessibilidade Motora

Resumo do projeto — arquitetura, decisões de escopo e plano de MVP de 3 meses.

## Visão geral

Sistema de agente controlado por voz capaz de realizar ações no computador — pesquisas, cliques de mouse, digitação, abertura de aplicativos, entre outros — a partir de comandos falados. O foco do projeto é acessibilidade: atender pessoas com deficiência motora, **parcial ou total**, que têm dificuldade ou impossibilidade de usar mouse e teclado convencionais.

Por trás da orquestração está um modelo de IA (LLM) responsável por interpretar a intenção por trás dos comandos de voz e traduzi-la em ações concretas no sistema operacional — não um conjunto fixo de comandos de sintaxe rígida.

**Benchmark de referência:** Windows Voice Access. O diferencial do projeto é a compreensão de linguagem natural e comandos compostos (ex: *"abre o e-mail e responde pro João dizendo que vou chegar atrasado"*), em vez da sintaxe fixa do Voice Access (*"clique 7"*, *"role para baixo"*).

## Como funciona (conceito)

1. Usuário ativa o agente por voz (wake word) e fala um comando
2. O agente transcreve a fala e interpreta a intenção
3. Antes de agir, o agente **confirma por voz** o que entendeu
4. Após confirmação, executa a ação (clique, digitação, abertura de app etc.)
5. Um comando de emergência ("parar", "cancelar") interrompe qualquer ação a qualquer momento, com prioridade máxima

## Arquitetura

O sistema é dividido em duas camadas, porque containers Docker não têm acesso nativo a mouse, teclado e tela do Windows — isso exige um componente nativo no host com permissões reais de input, separado da camada de "inteligência", que pode ser containerizada.

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│   HOST WINDOWS (nativo)     │        │   DOCKER (containers)         │
│                              │        │                                │
│  • Wake word + STT local    │◄──────►│  • Orquestrador / router      │
│  • Percepção de tela         │        │  • LLM local (Ollama) [fase 2]│
│    (UI Automation)           │        │  • Confirmação (TTS) + sessão │
│  • Executor de ações         │        │                                │
│    (clique, digitação, stop) │        └──────────────┬─────────────────┘
└─────────────────────────────┘                       │
                                                        ▼
                                          ┌──────────────────────────┐
                                          │   Claude API (nuvem)      │
                                          │   comandos complexos      │
                                          └──────────────────────────┘
```

| Serviço | Onde roda | Responsabilidade |
|---|---|---|
| Wake word + STT local | Host | Ativação por voz e transcrição, baixa latência |
| Percepção de tela | Host | Lê a árvore de elementos clicáveis (UI Automation) |
| Executor de ações | Host | Executa clique, digitação e o comando de parada de emergência |
| Orquestrador / router | Docker | Decide entre regra determinística (rápida) ou LLM (nuvem/local) |
| LLM local (Ollama) | Docker | Comandos simples e frequentes — **planejado para fase 2** |
| Confirmação (TTS) + sessão | Docker | Pergunta antes de agir; guarda contexto e histórico (Redis) |
| Claude API | Nuvem | Interpreta comandos complexos e ambíguos |

## Decisões de design

- **Perfil de usuário do MVP:** ambos os perfis (deficiência parcial e total) atendidos desde o início
- **Processamento de IA:** híbrido — caminho rápido local para comandos simples, nuvem (Claude API) para comandos complexos
- **Tratamento de erro de interpretação:** confirmação por voz obrigatória antes de qualquer ação

## Escopo do MVP (3 meses)

**Entra:**
- Windows 10/11, apenas português (PT-BR)
- 3 apps-alvo: navegador (Chrome/Edge), Explorer, Notepad ou Word
- ~20-30 comandos priorizados (abrir app, clicar, digitar, rolar, fechar, desfazer)
- Confirmação por voz antes de agir + comando de emergência
- Roteamento híbrido qualitativo: regras determinísticas (substituindo o LLM local por enquanto) + Claude API

**Fica para depois (fase 2):**
- LLM local (Ollama)
- Fallback de visão computacional para apps sem árvore de acessibilidade
- Macros e perfis personalizados por usuário
- Outros idiomas e outros sistemas operacionais

## Equipe e recursos

- **Time:** pequeno, de 3 a 5 pessoas
- **Dedicação:** fins de semana e horas livres (part-time)
- **Orçamento:** limitado — poucos dólares por mês, concentrado na API da Claude (o diferencial de compreensão de linguagem natural do produto)

## Cronograma resumido (12 fins de semana)

| Mês | Foco | Marco |
|---|---|---|
| 1 | Fundação: wake word, STT local, UI Automation, executor, conexão host↔orquestrador | Loop básico com 5 comandos fixos, sem IA nem confirmação |
| 2 | Inteligência: integração com Claude API, confirmação por voz, sessão/Redis, comando de emergência | Loop completo com confirmação, fallback de LLM e parada de emergência |
| 3 | Consolidação: docker-compose, empacotamento do host agent, testes com usuários reais, documentação | MVP validado com usuários reais nos 3 apps-alvo |

## Principais riscos

- **Latência ponta a ponta** entre falar e a ação acontecer
- **Precisão do STT em PT-BR** com fala atípica (deficiências motoras que coexistem com dificuldades de fala)
- **Cobertura limitada da UI Automation** em apps sem árvore de acessibilidade completa
- **Fadiga de confirmação** em sequências longas de comandos
- **Scope creep** — risco elevado em time part-time de fim de semana; a lista de itens "fica para depois" deve ser tratada como compromisso do time

## Critério de sucesso do MVP

Um usuário com deficiência motora consegue, só de voz, abrir o navegador, navegar até um site, digitar uma busca e fechar o app — com confirmação em cada passo e possibilidade de cancelar/parar a qualquer momento — nos 3 apps-alvo, com latência aceitável e sem travar.
