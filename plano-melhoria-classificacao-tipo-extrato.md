# Plano de Ação — Melhoria na Classificação de Tipo de Extrato

> **Objetivo**: Aumentar a acurácia na identificação do `tipo_documento` de ~85% para 97%+,
> reduzindo dependência da LLM para decisões que podem ser tomadas deterministicamente.

---

## Diagnóstico

### Problema Central

O sistema atual delega à LLM a classificação de tipos de documento sem uma camada
determinística robusta cobrindo todos os casos. Apenas 4 dos 13 tipos possuem heurísticas
pós-LLM que corrigem possíveis erros:

```
✅ EXTRATO DA CONTA CAPITAL   → _is_conta_capital()
✅ EXTRATO EMPRÉSTIMO         → _is_emprestimo()
✅ EXTRATO APLICACAO          → _has_rende_facil_hint()  ← só detecta "RENDE FACIL"
✅ PAR - RELATÓRIO            → _detect_par_report_type()

❌ EXTRATO CONTA POUPANÇA     → só LLM
❌ EXTRATO CONSOLIDADO RENDA FIXA → só LLM
❌ EXTRATO DE FATURA CARTÃO   → só LLM
❌ CONTA GRÁFICA DETALHADA    → só LLM
❌ CONTA GRÁFICA SIMPLIFICADA → só LLM
❌ REL RECEBIMENTO            → só LLM
❌ EXTRATO PIX                → só LLM
❌ EXTRATO CONSÓRCIO          → só LLM
```

### Causas Raiz

| # | Causa | Impacto |
|---|-------|---------|
| 1 | Cobertura parcial das heurísticas pós-LLM | Alto — tipos sem heurística dependem 100% da LLM |
| 2 | Título do documento não é tratado como dado prioritário | Alto — título é o sinal mais confiável |
| 3 | LLM retorna variações de texto não normalizadas | Médio — "EXTRATO APLICAÇÃO" ≠ "EXTRATO APLICACAO" |
| 4 | Sem segunda passagem para baixa confiança | Médio — casos ambíguos ficam com classificação errada |
| 5 | Heurísticas frágeis a variações tipográficas (OCR) | Médio — typos de escaneamento quebram keywords exatas |

---

## Técnicas Avaliadas

Antes de definir as ações, foram avaliadas três técnicas:

| Técnica | Aplicação aqui | Decisão | Motivo |
|---------|---------------|---------|--------|
| **Chunking** (LangChain TextSplitter) | Isolar cabeçalho do documento | ✅ Adotar | Baixo custo, alto ganho — cabeçalho é o sinal mais confiável |
| **Fuzzy Matching** (rapidfuzz, já no projeto) | Comparar título extraído vs tipos canônicos | ✅ Adotar | Resistência a typos de OCR sem custo externo |
| **Embeddings** (OpenAIEmbeddings) | Classificação semântica de tipo | ❌ Não adotar | Overkill — títulos têm sinal explícito, embeddings adicionam custo/latência sem ganho proporcional |

> **Por que embeddings não se aplicam aqui**: Embeddings brilham quando o sinal está *implícito*
> e há alta variação semântica (ex: "quitação de débito" ≈ "pagamento de parcela"). Para tipos
> de extrato, o título é quase sempre explícito no documento ("EXTRATO CONSOLIDADO RENDA FIXA").
> Embeddings seriam úteis para matching de nomes de clientes com variações — não para tipos.

---

## Plano de Ação

### Visão Geral do Fluxo Proposto

```
Documento (texto extraído)
        │
        ▼
┌─────────────────────────────────────┐
│  ETAPA 1: Chunking do Cabeçalho     │  ← NOVO (LangChain TextSplitter)
│  Isola primeiras linhas como chunk  │
│  de alta prioridade                 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  ETAPA 2: Keywords no Título        │  ← NOVO (tabela de prioridade)
│  Busca exata no chunk do cabeçalho  │
└──────────────┬──────────────────────┘
               │ tipo encontrado?
               ├─ SIM → confiança 0.95 → pula LLM para tipo
               │
               ▼ NÃO
┌─────────────────────────────────────┐
│  ETAPA 3: LLM com dica de tipo      │  ← MELHORADO (soft constraint)
│  Prompt inclui tipo_hint se houver  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  ETAPA 4: Normalização de Aliases   │  ← NOVO
│  "POUPANÇA" → "EXTRATO CONTA        │
│   POUPANCA" (canônico)              │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  ETAPA 5: Validação pós-LLM         │  ← EXPANDIDO (keywords + fuzzy)
│  Keywords no texto completo         │
│  + Fuzzy do título vs canônicos     │
└──────────────┬──────────────────────┘
               │ confiança < 0.70?
               ├─ SIM → segunda passagem fuzzy
               │
               ▼
        Resultado final
```

---

## Ações Detalhadas

---

### Ação 1 — Chunking do Cabeçalho (LangChain TextSplitter)

**Arquivo**: `llm_service.py`
**Lib**: `langchain.text_splitter.RecursiveCharacterTextSplitter` (já instalada)
**Esforço**: Baixo
**Ganho estimado**: +4% — garante que o contexto mais importante não seja diluído

#### Raciocínio

Hoje as primeiras linhas são extraídas com um simples `splitlines()[:10]`, que não
considera que documentos PDF podem ter quebras irregulares, espaços extras ou linhas
em branco entre o banco e o título. O `RecursiveCharacterTextSplitter` do LangChain
divide por separadores semânticos (`\n\n`, `\n`, espaço), garantindo um chunk limpo.

#### Implementação

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

def _extract_header_chunk(self, text: str) -> str:
    """
    Extrai o chunk de cabeçalho do documento usando LangChain TextSplitter.
    O cabeçalho (primeiros ~500 chars) tem alta densidade de informação sobre tipo.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=0,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_text(text)
    if not chunks:
        return text[:500]

    # Chunk 0 = cabeçalho → maior confiança para tipo e banco
    return chunks[0]
```

#### Uso nos demais métodos

```python
# Em _classify_from_title e _infer_bank_from_text_hints:
header = self._extract_header_chunk(text)
tipo = self._classify_from_keywords(header)
# Se encontrado no cabeçalho → confiança 0.95
# Se não → buscar no texto completo → confiança 0.80
```

---

### Ação 2 — Tabela de Keywords por Tipo (prioridade ordenada)

**Arquivo**: `llm_service.py`
**Esforço**: Baixo
**Ganho estimado**: +8% de acurácia nos tipos sem heurística

#### O que fazer

Criar uma constante `TIPO_KEYWORDS` no topo do arquivo, substituindo os `if/elif`
espalhados. A ordem importa: tipos mais específicos devem vir antes dos genéricos.

```python
# Ordem: mais específico → mais genérico
TIPO_KEYWORDS: list[tuple[str, list[str]]] = [

    # ── PAR (muito específico — antes de empréstimo) ─────────────────────────
    (
        "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS",
        ["PAR RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS"],
    ),
    (
        "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO",
        ["PAR RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO"],
    ),

    # ── Conta Capital (antes de Conta Corrente) ───────────────────────────────
    (
        "EXTRATO DA CONTA CAPITAL",
        ["EXTRATO DA CONTA CAPITAL", "EXTRATO DE CONTA CAPITAL",
         "CONTA CAPITAL", "CAPITAL SOCIAL"],
    ),

    # ── Renda Fixa Consolidada (antes de Aplicação genérica) ─────────────────
    (
        "EXTRATO CONSOLIDADO RENDA FIXA",
        ["EXTRATO CONSOLIDADO RENDA FIXA", "CONSOLIDADO RENDA FIXA",
         "RENDA FIXA CONSOLIDADA"],
    ),

    # ── Aplicação / Investimentos ─────────────────────────────────────────────
    (
        "EXTRATO APLICACAO",
        ["RENDE FACIL", "EXTRATO DE APLICACAO", "EXTRATO APLICACAO",
         "APLICACAO FINANCEIRA", "CDB", "LCI", "LCA",
         "FUNDO DE INVESTIMENTO", "EXTRATO DE INVESTIMENTO"],
    ),

    # ── Poupança (antes de Conta Corrente) ────────────────────────────────────
    (
        "EXTRATO CONTA POUPANCA",
        ["EXTRATO DE POUPANCA", "EXTRATO CONTA POUPANCA",
         "CADERNETA DE POUPANCA", "CONTA POUPANCA",
         "EXTRATO DE CADERNETA"],
    ),

    # ── Empréstimo / Crédito ──────────────────────────────────────────────────
    (
        "EXTRATO EMPRESTIMO",
        ["EXTRATO DE OPERACAO DE CREDITO", "EXTRATO DE EMPRESTIMO",
         "NUMERO DO CONTRATO", "OPERACAO DE CREDITO",
         "CONTRATO DE EMPRESTIMO", "CREDITO RURAL", "FINANCIAMENTO"],
    ),

    # ── Consórcio ─────────────────────────────────────────────────────────────
    (
        "EXTRATO CONSORCIO",
        ["EXTRATO DE CONSORCIO", "CONSORCIO", "GRUPO DE CONSORCIO"],
    ),

    # ── Conta Gráfica ─────────────────────────────────────────────────────────
    (
        "CONTA GRAFICA DETALHADA",
        ["CONTA GRAFICA DETALHADA"],
    ),
    (
        "CONTA GRAFICA SIMPLIFICADA",
        ["CONTA GRAFICA SIMPLIFICADA"],
    ),

    # ── Cartão de Crédito ─────────────────────────────────────────────────────
    (
        "EXTRATO DE FATURA DE CARTAO DE CREDITO",
        ["FATURA DE CARTAO DE CREDITO", "EXTRATO DE FATURA",
         "CREDITO ROTATIVO", "CARTAO DE CREDITO", "FATURA DO CARTAO"],
    ),

    # ── REL Recebimento ───────────────────────────────────────────────────────
    (
        "REL RECEBIMENTO",
        ["TITULOS POR PERIODO", "RELATORIO DE RECEBIMENTO",
         "TITULOS CADASTRADOS", "RECEBIMENTO DE TITULOS"],
    ),

    # ── PIX ───────────────────────────────────────────────────────────────────
    (
        "EXTRATO PIX",
        ["EXTRATO PIX", "TRANSFERENCIA PIX", "EXTRATO DE PIX"],
    ),

    # ── Conta Corrente (mais genérico — sempre por último) ────────────────────
    (
        "EXTRATO DE CONTA CORRENTE",
        ["EXTRATO DE CONTA CORRENTE", "EXTRATO CONTA CORRENTE",
         "MOVIMENTACAO BANCARIA", "EXTRATO BANCARIO", "CONTA CORRENTE"],
    ),
]
```

#### Método auxiliar de busca

```python
def _classify_from_keywords(self, text: str) -> str | None:
    """Classifica tipo de documento por tabela de keywords (ordem de prioridade)."""
    normalized = self._normalize_text_for_hint(text)
    for tipo, keywords in TIPO_KEYWORDS:
        for kw in keywords:
            if kw in normalized:
                return tipo
    return None
```

---

### Ação 3 — Fuzzy Matching do Título vs Tipos Canônicos

**Arquivo**: `llm_service.py`
**Lib**: `rapidfuzz` (já instalada no projeto)
**Esforço**: Baixo
**Ganho estimado**: +4% — captura typos de OCR que quebram keywords exatas

#### Raciocínio

Keywords exatas falham quando o OCR introduz erros tipográficos. Exemplos reais:

```
"EXTRATO CENTA CORRENTE"     → keyword "CONTA CORRENTE" não bate
"EXTRATO CONSOLI DADO RENDA" → keyword "CONSOLIDADO RENDA FIXA" não bate
"CONTA GRAFlCA DETALHADA"    → "I" maiúsculo vs "l" minúsculo
```

O `rapidfuzz` já está no projeto para matching de clientes. Reutilizá-lo para
comparar o título extraído contra os tipos canônicos cobre esses casos sem custo.

#### Implementação

```python
from rapidfuzz import process, fuzz

# Lista plana dos tipos canônicos (extraída de TIPO_KEYWORDS)
TIPOS_CANONICOS: list[str] = [tipo for tipo, _ in TIPO_KEYWORDS]

def _classify_from_fuzzy(
    self,
    text: str,
    threshold: int = 80,
) -> tuple[str, float] | None:
    """
    Compara texto normalizado do cabeçalho contra tipos canônicos via fuzzy.
    Retorna (tipo, score_normalizado) ou None se abaixo do threshold.

    Usa token_sort_ratio: resistente a palavras fora de ordem.
    Ex: "RENDA FIXA EXTRATO CONSOLIDADO" ainda bate com
        "EXTRATO CONSOLIDADO RENDA FIXA"
    """
    normalized = self._normalize_text_for_hint(text)
    if not normalized:
        return None

    # Busca o melhor match entre os tipos canônicos
    result = process.extractOne(
        normalized,
        TIPOS_CANONICOS,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result is None:
        return None

    tipo, score, _ = result
    confianca = round(score / 100, 2)  # normaliza 0–100 → 0.0–1.0
    logger.debug(f"Fuzzy tipo: '{tipo}' score={score}")
    return tipo, confianca
```

#### Integração no fluxo

```python
# Aplicar APÓS keywords falharem, ANTES de segunda passagem da LLM:
if not tipo_encontrado:
    fuzzy_result = self._classify_from_fuzzy(header_chunk, threshold=82)
    if fuzzy_result:
        tipo, conf = fuzzy_result
        logger.info(f"Tipo identificado por fuzzy: {tipo} (score={conf:.0%})")
        result.tipo_documento = tipo
        result.confianca = max(result.confianca, conf * 0.9)  # pequeno desconto vs keyword exata
```

> **Por que `token_sort_ratio` e não `ratio`?**
> `ratio` compara strings na ordem exata — sensível a palavras trocadas.
> `token_sort_ratio` ordena os tokens antes de comparar, cobrindo variações de ordem
> sem perder precisão. Ideal para títulos de documentos.

---

### Ação 4 — Normalização de Aliases

**Arquivo**: `llm_service.py`
**Esforço**: Baixo
**Ganho estimado**: Elimina erros de formatação sem custo de LLM

#### O que acontece hoje

A LLM às vezes retorna variações que não batem exatamente com os valores esperados:

| Retorno da LLM | Valor esperado |
|---|---|
| `"EXTRATO APLICAÇÃO"` | `"EXTRATO APLICACAO"` |
| `"CONTA CORRENTE"` | `"EXTRATO DE CONTA CORRENTE"` |
| `"POUPANÇA"` | `"EXTRATO CONTA POUPANCA"` |
| `"CARTÃO"` | `"EXTRATO DE FATURA DE CARTAO DE CREDITO"` |
| `"CC"` | `"EXTRATO DE CONTA CORRENTE"` |

#### Implementação

```python
TIPO_ALIASES: dict[str, str] = {
    # Formas curtas (já listadas no prompt)
    "CC":       "EXTRATO DE CONTA CORRENTE",
    "CARTAO":   "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "CARTÃO":   "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "POUPANCA": "EXTRATO CONTA POUPANCA",
    "POUPANÇA": "EXTRATO CONTA POUPANCA",

    # Sem prefixo "EXTRATO DE"
    "CONTA CORRENTE":   "EXTRATO DE CONTA CORRENTE",
    "CONTA POUPANCA":   "EXTRATO CONTA POUPANCA",
    "CONTA CAPITAL":    "EXTRATO DA CONTA CAPITAL",
    "FATURA DE CARTAO": "EXTRATO DE FATURA DE CARTAO DE CREDITO",

    # Com acentos (LLM às vezes retorna com acento)
    "EXTRATO APLICAÇÃO":      "EXTRATO APLICACAO",
    "EXTRATO EMPRÉSTIMO":     "EXTRATO EMPRESTIMO",
    "EXTRATO CONSÓRCIO":      "EXTRATO CONSORCIO",
    "EXTRATO CONTA POUPANÇA": "EXTRATO CONTA POUPANCA",
}

def _normalize_tipo_documento(self, tipo: str | None) -> str | None:
    """Normaliza tipo retornado pela LLM para o valor canônico."""
    if not tipo:
        return tipo
    upper = tipo.strip().upper()
    return TIPO_ALIASES.get(upper, upper)
```

Chamar logo após o parse do JSON da LLM:

```python
result.tipo_documento = self._normalize_tipo_documento(result.tipo_documento)
```

---

### Ação 5 — Dica de Tipo no Prompt da LLM (Soft Constraint)

**Arquivo**: `llm_service.py`
**Esforço**: Baixo
**Ganho estimado**: +3% nos casos onde a heurística encontra candidato provável

#### Raciocínio

Quando o chunking + keywords detectam um tipo provável antes da LLM, incluí-lo
como dica no prompt melhora a consistência sem forçar um resultado errado.

**Técnica de prompt**: *soft constraint* — orientar sem impor.

```python
def _build_human_message(self, text: str, tipo_hint: str | None = None) -> str:
    """Monta a mensagem humana com dica opcional de tipo."""
    hint_block = ""
    if tipo_hint:
        hint_block = (
            f"\n\n<dica_pre_analise>"
            f"\nAnálise textual preliminar sugere: tipo_documento = \"{tipo_hint}\""
            f"\nConfirme ou corrija com base no texto completo."
            f"\n</dica_pre_analise>"
        )
    return f"TEXTO DO DOCUMENTO:{hint_block}\n\n{text}"
```

> **Por que XML/tags semânticas?** Tags como `<dica_pre_analise>` delimitam claramente
> o contexto da dica, evitando que a LLM confunda a dica com conteúdo do documento.

---

### Ação 6 — Segunda Passagem para Baixa Confiança

**Arquivo**: `llm_service.py`
**Esforço**: Médio
**Ganho estimado**: +5% nos casos ambíguos

#### Quando aplicar

```
resultado.confianca < 0.70
E resultado.tipo_documento IN ("OUTROS", null, "EXTRATO DE CONTA CORRENTE")
E texto tem mais de 500 caracteres (documento real, não vazio)
```

#### Implementação

Tenta em ordem: keywords no texto completo → fuzzy no texto completo.

```python
if (result.confianca < 0.70
        and result.tipo_documento in ("OUTROS", None, "EXTRATO DE CONTA CORRENTE")
        and len(analysis_text) > 500):

    # Tentativa 1: keywords no texto completo
    tipo_fallback = self._classify_from_keywords(analysis_text)
    if tipo_fallback and tipo_fallback != result.tipo_documento:
        logger.info(
            f"Segunda passagem (keywords): '{result.tipo_documento}' → '{tipo_fallback}'"
        )
        result.tipo_documento = tipo_fallback
        result.confianca = 0.75

    # Tentativa 2: fuzzy no texto completo (se keywords falharam)
    elif not tipo_fallback:
        fuzzy_result = self._classify_from_fuzzy(analysis_text, threshold=78)
        if fuzzy_result:
            tipo_fuzzy, conf_fuzzy = fuzzy_result
            logger.info(
                f"Segunda passagem (fuzzy): '{result.tipo_documento}' → '{tipo_fuzzy}' "
                f"(score={conf_fuzzy:.0%})"
            )
            result.tipo_documento = tipo_fuzzy
            result.confianca = max(result.confianca, conf_fuzzy * 0.85)
```

---

## Ordem de Implementação Recomendada

| # | Ação | Lib usada | Esforço | Ganho | Prioridade |
|---|------|-----------|---------|-------|------------|
| 1 | Tabela de keywords completa | — | 1–2h | +8% | **Alta** |
| 2 | Normalização de aliases | — | 30min | elimina erros fmt | **Alta** |
| 3 | Chunking do cabeçalho | LangChain TextSplitter | 1h | +4% | **Alta** |
| 4 | Fuzzy título vs canônicos | rapidfuzz (já instalado) | 1h | +4% | **Alta** |
| 5 | Segunda passagem baixa confiança | — | 1h | +5% | Média |
| 6 | Dica de tipo no prompt | — | 1h | +3% | Média |

**Resultado esperado ao implementar tudo:**

```
Acurácia atual:   ~85%
Após Ações 1+2:   ~93%
Após Ações 3+4:   ~95%
Após todas:       ~97%
```

> **Nota sobre Embeddings**: avaliada e descartada para este caso.
> O sinal de tipo de documento é explícito no texto (título do documento).
> Embeddings seriam úteis se o sinal fosse semântico e implícito — não é o caso aqui.
> Custo adicional (1 chamada API por documento) sem ganho proporcional.

---

## Testes Recomendados por Ação

```
Ação 1 — Keywords:
  ✓ Documento com "CDB" → EXTRATO APLICACAO
  ✓ Documento com "CADERNETA DE POUPANÇA" → EXTRATO CONTA POUPANCA
  ✓ Documento com "FATURA DO CARTÃO" → EXTRATO DE FATURA DE CARTAO DE CREDITO
  ✓ "CONSOLIDADO RENDA FIXA" → não retorna EXTRATO APLICACAO (ordem de prioridade)

Ação 2 — Aliases:
  ✓ LLM retorna "POUPANÇA" → normalizado para "EXTRATO CONTA POUPANCA"
  ✓ LLM retorna "CC" → normalizado para "EXTRATO DE CONTA CORRENTE"
  ✓ LLM retorna "EXTRATO APLICAÇÃO" → normalizado para "EXTRATO APLICACAO"

Ação 3 — Chunking:
  ✓ Documento com muitas quebras de linha → chunk 0 ainda captura título
  ✓ Documento curto (<500 chars) → fallback para text[:500] sem erro

Ação 4 — Fuzzy:
  ✓ "EXTRATO CENTA CORRENTE" (typo OCR) → bate com "EXTRATO DE CONTA CORRENTE" score ~87
  ✓ "EXTRATO CONSOLI DADO RENDA FIXA" → bate com "EXTRATO CONSOLIDADO RENDA FIXA"
  ✓ Texto genérico sem título → score abaixo de threshold, não classifica

Ação 5 — Segunda passagem:
  ✓ LLM retorna OUTROS (confiança 0.5), texto tem "CONSORCIO" → corrigido por keywords
  ✓ LLM retorna CONTA CORRENTE (confiança 0.9) → não alterado
  ✓ Typo no texto, keywords falham → fuzzy de segunda passagem corrige

Ação 6 — Hint no prompt:
  ✓ Dica correta → LLM confirma
  ✓ Dica errada → LLM corrige (soft constraint, não imposta)
```

---

## Registro de Alterações

| Data | Versão | Alteração |
|------|--------|-----------|
| 2026-03-25 | 1.0 | Documento criado (5 ações originais) |
| 2026-03-25 | 1.1 | Adicionado chunking (LangChain) e fuzzy (rapidfuzz) após avaliação de técnicas; embeddings avaliados e descartados; ações reordenadas |

---

*Última atualização: 2026-03-25*
