# Features — Automação de Extratos

> Documento vivo: registra features **implementadas** e **planejadas**.
> Atualizar sempre que uma feature for concluída ou uma nova ideia surgir.

---

## Como usar este documento

| Símbolo | Significado |
|---------|-------------|
| ✅ | Implementado e em produção |
| 🔄 | Em desenvolvimento |
| 📋 | Planejado (tem plano de ação) |
| 💡 | Ideia / a avaliar |
| ⏸️ | Pausado / descartado temporariamente |

---

## Índice

1. [Extração e IA](#1-extração-e-ia)
2. [Identificação de Clientes (Matching)](#2-identificação-de-clientes-matching)
3. [Tipos de Documento](#3-tipos-de-documento)
4. [Formatos de Arquivo](#4-formatos-de-arquivo)
5. [Watcher e Automação](#5-watcher-e-automação)
6. [Simulação e Testes](#6-simulação-e-testes)
7. [Reversão](#7-reversão)
8. [Observabilidade e Logs](#8-observabilidade-e-logs)
9. [Performance e Cache](#9-performance-e-cache)
10. [Interface e API](#10-interface-e-api)
11. [Integrações Externas](#11-integrações-externas)

---

## 1. Extração e IA

### ✅ Extração LLM com dual-model routing
Documentos simples usam `gpt-4o-mini` (rápido/barato); complexos usam `gpt-4o` (preciso).
Critérios de complexidade: múltiplos bancos, sem cabeçalho, tipos ambíguos.
- **Economia**: ~36% no custo médio por requisição

### ✅ Prompts otimizados com estrutura XML
Prompts reduzidos de ~215 linhas para ~90 linhas com estrutura hierárquica XML.
Libera mais tokens para o documento (+15%) e melhora consistência das respostas.

### ✅ Few-shot dinâmico
Exemplos no prompt são selecionados conforme o tipo detectado no documento.
Evita desperdício de tokens com exemplos irrelevantes.

### ✅ Chain-of-Thought para casos complexos
Quando confiança < 0.7 ou caso complexo: força raciocínio explícito via tags `<thinking>`.
Melhoria de precisão estimada: +5–10% nos casos difíceis.

### ✅ Prompts especializados por tipo
Prompts distintos para: OFX, Conta Capital, Empréstimo, PAR (Sicoob).
Especialização aumenta precisão +10–15% frente ao prompt genérico.

### ✅ Fallback inteligente (fast → advanced → result vazio)
Se modelo rápido falha → tenta modelo avançado automaticamente.
Se ambos falham → retorna resultado com confiança 0.0 (sem bloquear o fluxo).

### ✅ Validação pós-extração (6 checagens)
Detecta inconsistências após extração da LLM:
CNPJ inválido, Conta Capital com número de conta, banco vazio, tipo vazio, etc.
Gera warnings sem bloquear (não-destrutivo).

### ✅ OCR de cabeçalho (VisionService)
Se texto não tem marcadores de banco/conta → extrai header via OCR antes de enviar à LLM.
Garante que o contexto mais importante do documento não seja perdido.

### ✅ Identificação de banco por logo/imagem
Quando texto não identifica banco → envia imagem da 1ª página para `gpt-4o` vision.
Detecta logos de: Sicoob, Sicredi, Cresol, Banco do Brasil, Bradesco, Itaú, Caixa, Santander.

### ✅ Heurísticas pós-LLM (banco e tipo)
Camada determinística que corrige saída da LLM com regras explícitas:
- Banco: keywords SICOOB, CRESOL, SISBR, BB (evita confusões LLM)
- Tipo: Conta Capital, Empréstimo, Rende Fácil, PAR
- Conta: remove matrícula quando é Conta Capital

### 📋 Tabela de keywords completa por tipo de documento
Expandir heurísticas pós-LLM para cobrir todos os 13 tipos (hoje só 4 têm heurística).
Incluir: Poupança, Renda Fixa, Cartão, Conta Gráfica, REL Recebimento, PIX, Consórcio.
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 1)

### 📋 Normalização de aliases de tipo (LLM → canônico)
Mapear variações de resposta da LLM para o valor padrão do sistema.
Ex: "EXTRATO APLICAÇÃO" → "EXTRATO APLICACAO", "CC" → "EXTRATO DE CONTA CORRENTE".
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 2)

### 📋 Chunking do cabeçalho via LangChain TextSplitter
Usar `RecursiveCharacterTextSplitter` para isolar o cabeçalho do documento como chunk de alta prioridade.
Resolve quebras irregulares de PDF que prejudicam a extração das primeiras linhas.
Lib: `langchain.text_splitter` (já instalada). Ganho estimado: +4%.
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 3)

### 📋 Fuzzy Matching do título vs tipos canônicos
Comparar título extraído do cabeçalho contra lista de tipos canônicos via `rapidfuzz` (já no projeto).
Resistente a typos de OCR: "EXTRATO CENTA CORRENTE" → bate com "EXTRATO DE CONTA CORRENTE".
Usa `token_sort_ratio` para cobrir variações de ordem de palavras. Ganho estimado: +4%.
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 4)

### 📋 Segunda passagem para baixa confiança
Quando confiança < 0.70 e tipo = "OUTROS" → keywords no texto completo → fuzzy como último recurso.
Evita que documentos ambíguos fiquem classificados como "OUTROS" desnecessariamente.
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 5)

### 📋 Dica de tipo no prompt da LLM (soft constraint)
Se heurística detecta tipo antes da LLM, incluir como dica no prompt via tag `<dica_pre_analise>`.
LLM pode confirmar ou corrigir — não é imposto. Melhoria esperada: +3%.
→ Ver plano detalhado em `plano-melhoria-classificacao-tipo-extrato.md` (Ação 6)

### ❌ Embeddings para classificação de tipo — AVALIADO E DESCARTADO
Técnica avaliada e descartada: o sinal de tipo é explícito no título do documento,
embeddings adicionariam custo (+1 chamada API/doc) sem ganho proporcional.
Embeddings fazem sentido para matching de nomes de clientes com variações semânticas — não aqui.

### 💡 Modelo de classificação local (sem LLM) para tipos simples
Treinar um classificador leve (ex: TF-IDF + SVM ou regex ponderado) nos documentos já processados.
Para tipos com sinal textual forte (Conta Capital, OFX, PAR), dispensar LLM totalmente.
Reduz custo e latência para ~20% dos documentos.

### 💡 Extração de período (mês/ano) a partir do texto
Hoje o período usa "mês anterior" como default. Extrair a data de referência diretamente
do cabeçalho do documento aumenta a precisão do arquivamento.

### 💡 Contagem real de tokens (tiktoken) por chamada
Implementar logging do uso real de tokens via `tiktoken` por documento.
Permite dashboard de custo granular por tipo de documento e por banco.

---

## 2. Identificação de Clientes (Matching)

### ✅ Matching por CNPJ exato (score 100)
Prioridade máxima. Se CNPJ do documento bate com CNPJ da planilha → cliente identificado.

### ✅ Matching por Agência + Conta + Banco
Segundo critério. Normaliza números (remove zeros, hífens, pontos) antes de comparar.
Suporte a dígito verificador opcional na agência.

### ✅ Matching por similaridade de nome (rapidfuzz)
Terceiro critério. Threshold configurável (default: 85% de similaridade).
Evita falsos positivos em empresas com nomes parecidos.

### ✅ Matching preferencial por Conta para OFX
OFX sempre tenta conta primeiro (dado estruturado confiável), depois CNPJ e nome.

### ✅ Inferência de banco pela planilha de clientes
Se banco não identificado, cruza agência+conta com planilha para deduzir banco.
Retorna banco apenas se resultado for unívoco (1 match).

### 💡 Score de confiança por método de matching
Retornar junto ao resultado o score numérico de cada método tentado.
Permite que o sistema tome decisões diferentes para matches de baixa confiança
(ex: mover para pasta NAO_IDENTIFICADOS ao invés de identificado com score 60).

### 💡 Matching por número de contrato (empréstimos)
Para EXTRATO EMPRÉSTIMO, usar número do contrato como critério adicional de matching.
Útil quando cliente tem múltiplas contas no mesmo banco.

### 💡 Sugestão de cliente em casos não identificados
Quando nenhum cliente é encontrado, retornar os 3 melhores candidatos com score.
Permite que o usuário confirme manualmente via interface ao invés de reprocessar.

---

## 3. Tipos de Documento

### ✅ Tipos implementados e reconhecidos

| Tipo | Heurística pós-LLM | Prompt especializado |
|------|-------------------|---------------------|
| EXTRATO DE CONTA CORRENTE | ❌ só LLM | ❌ |
| EXTRATO DA CONTA CAPITAL | ✅ `_is_conta_capital` | ✅ |
| EXTRATO CONTA POUPANÇA | ❌ só LLM | ❌ |
| EXTRATO APLICACAO | ✅ `_has_rende_facil_hint` | ❌ |
| EXTRATO CONSOLIDADO RENDA FIXA | ❌ só LLM | ❌ |
| EXTRATO DE FATURA DE CARTÃO | ❌ só LLM | ❌ |
| REL RECEBIMENTO | ❌ só LLM | ❌ |
| CONTA GRÁFICA DETALHADA | ❌ só LLM | ❌ |
| CONTA GRÁFICA SIMPLIFICADA | ❌ só LLM | ❌ |
| PAR - PARCELAS LIQUIDADAS | ✅ `_detect_par_report_type` | ❌ |
| PAR - PARCELAS EM ABERTO | ✅ `_detect_par_report_type` | ❌ |
| EXTRATO EMPRÉSTIMO | ✅ `_is_emprestimo` | ✅ |
| EXTRATO PIX | ❌ só LLM | ❌ |
| EXTRATO CONSÓRCIO | ❌ só LLM | ❌ |

### 📋 Adicionar heurísticas para os tipos sem cobertura
9 tipos dependem exclusivamente da LLM. Adicionar keywords determinísticas.
→ Ver plano em `plano-melhoria-classificacao-tipo-extrato.md`

### 💡 Tipo "BOLETO / COBRANÇA"
Documentos de cobrança/boleto bancário aparecem na pasta mas não têm tipo definido.
Avaliar se merece categoria própria ou cai em "OUTROS".

### 💡 Subtipo por banco para Conta Corrente
Alguns bancos têm layouts muito específicos (ex: Sicoob Conta Corrente vs BB Conta Corrente).
Subtipos ajudariam a escolher o prompt mais adequado e melhorar extração de campos.

---

## 4. Formatos de Arquivo

### ✅ PDF (pdfplumber + PyPDF2 fallback)
Extração principal via pdfplumber (preserva tabelas). Fallback para PyPDF2.

### ✅ OFX (Open Financial Exchange)
Parser de texto com detecção automática por conteúdo (tags SGML e XML).
Prompt especializado para extração de agência/conta de tags estruturadas.

### ✅ Excel (.xlsx, .xls, .ods)
Extração direta via pandas. Para Sicoob: extração sem LLM (0 tokens).
Detecta: Associado, Cooperativa, Conta Corrente, CNPJ, Período.

### ✅ ZIP (com PDFs e OFX internos)
Extração automática com validação de magic bytes.
Relatório de auditoria: extraídos, ignorados, erros.

### ✅ CSV, TXT, HTML, XML, JSON
Leitura como texto plano e envio para LLM.

### 💡 Suporte a imagens (JPG, PNG) com OCR
Alguns extratos são enviados como foto/escaneamento sem texto embutido.
Usar VisionService para OCR completo do documento.

### 💡 Extração de tabelas de PDFs escaneados (Vision)
PDFs escaneados têm texto inextrável por pdfplumber.
Usar vision para extrair tabelas de movimentações (útil para validar períodos).

---

## 5. Watcher e Automação

### ✅ Watcher de pasta com subpastas por banco
Monitora pasta configurável continuamente. Detecta novos arquivos automaticamente.
Suporte a subpastas: BANCO DO BRASIL, BRADESCO, CAIXA, CRESOL, ITAU, SANTANDER, SICREDI, SICOOB, OUTROS.

### ✅ Controle de pausa durante simulação
Flag `_simulation_active` pausa o watcher durante simulações.
Try-finally garante retomada mesmo em caso de erro. Evita que arquivos sejam movidos.

### ✅ Start/Stop manual via endpoint
`POST /extratos/watch/start` e `POST /extratos/watch/stop`.
Status em `/extratos/watch/status` com campo `paused_for_simulation`.

### ✅ Filtro de arquivos por padrão de nome no watcher
Permitir configurar regex/glob de quais arquivos o watcher deve processar.
Ex: ignorar arquivos que começam com `~$` (temporários do Office) ou `._`.

### ✅ Reprocessamento automático de falhas
Manter fila de arquivos que falharam e tentar novamente após intervalo configurável.
Útil para falhas temporárias de API ou banco de dados.

### ✅ Watcher com debounce configurável
Hoje o debounce para detectar mudanças recentes é fixo.
Tornar configurável via `.env` para ajustar conforme velocidade do servidor.

### 💡 Notificação de arquivos não identificados
Quando um arquivo vai para NAO_IDENTIFICADOS, enviar notificação (email, webhook, Slack).
Permite ação proativa ao invés de descobrir tardiamente.

---

## 6. Simulação e Testes

### ✅ Simulação de arquivo individual
`POST /extratos/simular-arquivo` — processa sem salvar, mostra destino calculado.
Retorna: cliente, banco, tipo, período, caminho_destino, confiança.

### ✅ Simulação em lote (todos os extratos)
`POST /extratos/simular-todos` — processa todos os arquivos da pasta em modo simulação.

### ✅ Interface HTML de simulação
`GET /extratos/simular` — página para simular via browser sem usar terminal.

### 💡 Comparação antes/depois na simulação
Mostrar resultado atual (como está) vs resultado simulado (como ficaria).
Útil para validar antes de aplicar mudanças de configuração.

### 💡 Exportar resultado de simulação em lote para Excel
Ao simular todos, gerar planilha com: arquivo, cliente identificado, tipo, destino, confiança.
Permite revisão humana antes de confirmar processamento em produção.

### 💡 Replay de processamento histórico
Reprocessar arquivos já salvos usando nova versão da IA/regras.
Compara resultado novo vs resultado original, flagga divergências para revisão.

---

## 7. Reversão

### ✅ Reversão por ID de processamento
Desfaz movimentação de arquivo de um processamento específico.

### ✅ Reversão em lote (múltiplos IDs)
Desfaz múltiplos processamentos de uma vez.

### ✅ Reversão dos últimos N processamentos
Conveniência para desfazer operações recentes rapidamente.

### 💡 Reversão com confirmação visual
Antes de reverter, mostrar lista de arquivos que serão movidos de volta.
Evitar reversões acidentais de lotes grandes.

### 💡 Histórico de reversões com rastreabilidade
Registrar quem reverteu, quando, e qual foi o motivo.
Facilita auditoria em casos de erro recorrente.

---

## 8. Observabilidade e Logs

### ✅ Logs estruturados em MySQL
Campos: arquivo, status, cliente, banco, agência, conta, tipo, período, método, confiança, hash.
Separados em produção e teste.

### ✅ Estatísticas de processamento
Total, sucesso, falhas, não-identificados. Agrupável por cliente, banco, período.

### ✅ WebSocket para atualizações em tempo real
4 canais: produção, teste, extratos, extratos-teste.
Emite eventos de progresso durante processamento de lotes.

### ✅ Métricas de LLM (custo, latência, confiança)
Tracking por sessão: custo total, latência média, taxa de cache hit.

### 💡 Dashboard de métricas por tipo de documento
Mostrar taxa de acerto (confiança média) por tipo de documento.
Identifica quais tipos estão com performance ruim e precisam de atenção.

### 💡 Alerta de degradação de confiança
Se a média de confiança cair abaixo de threshold nos últimos N processamentos,
emitir alerta (log de nível ERROR, webhook, ou notificação).

### 💡 Log de erros por banco
Agrupar erros por banco para identificar se um banco específico está causando
falhas recorrentes (ex: mudança de layout do extrato).

### 💡 Exportação de logs para Excel/CSV
Permitir download dos logs filtrados diretamente pela interface.

---

## 9. Performance e Cache

### ✅ Cache LRU para lista de clientes (5 minutos)
Evita releitura de Excel a cada requisição. Thread-safe com locks.

### ✅ Extração direta de Excel sem LLM (Sicoob)
Para arquivos .xlsx/.xls do Sicoob: extração estruturada via pandas, 0 tokens de LLM.
Redução de custo: -100%. Latência: -80%. Precisão: +5%.

### 💡 Cache de resultado de extração por hash do arquivo
Se o mesmo arquivo for reenviado, retornar resultado do cache imediatamente.
Hash MD5 como chave. Evita custo duplicado em reprocessamentos.

### 💡 Extração direta de Excel para outros bancos (Sicredi, Cresol)
Expandir a extração estruturada do Excel para outros bancos que também
exportam dados estruturados em planilha.

### 💡 Processamento paralelo de ZIPs com múltiplos arquivos
Hoje os arquivos dentro de um ZIP são processados sequencialmente.
Paralelizar com ThreadPoolExecutor reduz latência de lotes grandes.

### 💡 Pool de conexões LLM configurável
Permitir configurar número máximo de requisições paralelas à OpenAI.
Evita rate limit em processamentos de lote.

---

## 10. Interface e API

### ✅ Páginas HTML internas
Monitor produção, monitor teste, extratos, simulação, reversão.

### ✅ Endpoints de admin
Reload de clientes, reload de settings, limpeza de logs.

### ✅ WebSockets para live updates
Progresso em tempo real durante processamentos longos.

### ✅ Webhooks Make.com
Integração com automações Make (produção + teste).

### 💡 Endpoint de dry-run no upload
`POST /upload?dry_run=true` — processa e retorna resultado sem salvar arquivo.
Equivalente à simulação, mas via API (útil para integração).

### 💡 Paginação nos endpoints de listagem de logs
`GET /logs` retorna todos os logs. Adicionar `?page=1&limit=50` para não sobrecarregar.

### 💡 Filtro avançado nos logs
Filtrar por: data, banco, tipo de documento, método de matching, confiança mínima.
Facilita auditoria e identificação de padrões de erro.

### ✅ Endpoint de estatísticas por banco
`GET /stats/banco/{banco}` — retorna taxa de sucesso, confiança média, tipos mais comuns.

---

## 11. Integrações Externas

### ✅ OpenAI (LLM + Vision)
gpt-4o (avançado) + gpt-4o-mini (rápido) via LangChain.
gpt-4o vision para OCR e identificação de logos.

### ✅ MySQL via SQLAlchemy
Logs de produção e teste. Connection pooling com pre-ping.

### ✅ Make.com (webhooks)
Webhooks de entrada e processamento background com job IDs.

### 💡 Integração com Google Drive / OneDrive
Monitorar pasta em nuvem ao invés de somente pasta local.
Útil para equipes que fazem download em máquinas diferentes.

### 💡 Notificações via Telegram / WhatsApp
Enviar resumo diário de processamentos (total, sucesso, falhas, não identificados).
Ou alerta imediato para arquivos não identificados.

### 💡 Exportação para sistema contábil (ERP)
Enviar dados extraídos (cliente, banco, conta, tipo, período) para sistema contábil via API.
Elimina etapa manual de lançamento.

---

## Registro de Versões

| Versão | Data | Principais mudanças |
|--------|------|---------------------|
| V1.0 | 2025 | Upload + LLM básico + matching simples |
| V2.0 | 2026-02 | Prompts XML, dual-model, few-shot dinâmico, tiktoken |
| V2.1 | 2026-02 | Cache LRU, métricas acumuladas, fallback inteligente, validação pós-extração |
| V2.2 | 2026-02 | Controle watcher durante simulação, extração direta Excel (Sicoob) |
| V2.3 | 2026-03 | *(próxima)* Classificação de tipo melhorada (keywords completas + chunking LangChain + fuzzy rapidfuzz) |

---

*Última atualização: 2026-03-25*
