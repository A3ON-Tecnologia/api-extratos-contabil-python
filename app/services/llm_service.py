"""
Serviço de integração com LLM via LangChain.

Utiliza OpenAI para extrair informações estruturadas de documentos.
"""

import hashlib
import json
import logging
import re
import threading
import unicodedata

try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ImportError:
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        RecursiveCharacterTextSplitter = None
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from rapidfuzz import fuzz, process

from app.config import get_settings
from app.services.client_service import ClientService
from app.utils.text import extract_numbers
from app.schemas.llm_response import LLMExtractionResult

logger = logging.getLogger(__name__)

TIPO_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS",
        ["PAR RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS"],
    ),
    (
        "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO",
        ["PAR RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO"],
    ),
    (
        "EXTRATO DA CONTA CAPITAL",
        [
            "EXTRATO DA CONTA CAPITAL",
            "EXTRATO DE CONTA CAPITAL",
            "CONTA CAPITAL",
            "CAPITAL SOCIAL",
        ],
    ),
    (
        "EXTRATO CONSOLIDADO RENDA FIXA",
        [
            "EXTRATO CONSOLIDADO RENDA FIXA",
            "CONSOLIDADO RENDA FIXA",
            "RENDA FIXA CONSOLIDADA",
        ],
    ),
    (
        "EXTRATO APLICACAO",
        [
            "RENDE FACIL",
            "EXTRATO DE APLICACAO",
            "EXTRATO APLICACAO",
            "APLICACAO FINANCEIRA",
            "CDB",
            "LCI",
            "LCA",
            "FUNDO DE INVESTIMENTO",
            "EXTRATO DE INVESTIMENTO",
        ],
    ),
    (
        "EXTRATO CONTA POUPANCA",
        [
            "EXTRATO DE POUPANCA",
            "EXTRATO CONTA POUPANCA",
            "CADERNETA DE POUPANCA",
            "CONTA POUPANCA",
            "EXTRATO DE CADERNETA",
        ],
    ),
    (
        "EXTRATO EMPRESTIMO",
        [
            "EXTRATO DE OPERACAO DE CREDITO",
            "EXTRATO DE EMPRESTIMO",
            "NUMERO DO CONTRATO",
            "OPERACAO DE CREDITO",
            "CONTRATO DE EMPRESTIMO",
            "CREDITO RURAL",
            "FINANCIAMENTO",
        ],
    ),
    (
        "EXTRATO CONSORCIO",
        ["EXTRATO DE CONSORCIO", "CONSORCIO", "GRUPO DE CONSORCIO"],
    ),
    ("CONTA GRAFICA DETALHADA", ["CONTA GRAFICA DETALHADA"]),
    ("CONTA GRAFICA SIMPLIFICADA", ["CONTA GRAFICA SIMPLIFICADA"]),
    (
        "EXTRATO DE FATURA DE CARTAO DE CREDITO",
        [
            "FATURA DE CARTAO DE CREDITO",
            "EXTRATO DE FATURA",
            "CREDITO ROTATIVO",
            "CARTAO DE CREDITO",
            "FATURA DO CARTAO",
        ],
    ),
    (
        "REL RECEBIMENTO",
        [
            "TITULOS POR PERIODO",
            "RELATORIO DE RECEBIMENTO",
            "TITULOS CADASTRADOS",
            "RECEBIMENTO DE TITULOS",
        ],
    ),
    ("EXTRATO PIX", ["EXTRATO PIX", "TRANSFERENCIA PIX", "EXTRATO DE PIX"]),
    (
        "EXTRATO DE CONTA CORRENTE",
        [
            "EXTRATO DE CONTA CORRENTE",
            "EXTRATO CONTA CORRENTE",
            "MOVIMENTACAO BANCARIA",
            "EXTRATO BANCARIO",
            "CONTA CORRENTE",
        ],
    ),
]

TIPOS_CANONICOS: list[str] = [tipo for tipo, _ in TIPO_KEYWORDS]

TIPO_ALIASES: dict[str, str] = {
    "CC": "EXTRATO DE CONTA CORRENTE",
    "CARTAO": "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "POUPANCA": "EXTRATO CONTA POUPANCA",
    "CONTA CORRENTE": "EXTRATO DE CONTA CORRENTE",
    "CONTA POUPANCA": "EXTRATO CONTA POUPANCA",
    "CONTA CAPITAL": "EXTRATO DA CONTA CAPITAL",
    "FATURA DE CARTAO": "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "EXTRATO APLICACAO": "EXTRATO APLICACAO",
    "EXTRATO EMPRESTIMO": "EXTRATO EMPRESTIMO",
    "EXTRATO CONSORCIO": "EXTRATO CONSORCIO",
    "EXTRATO CONTA POUPANCA": "EXTRATO CONTA POUPANCA",
}

# Prompt do sistema para extração de informações
SYSTEM_PROMPT = """Você é um assistente especializado em análise de documentos financeiros e bancários.
Sua tarefa é extrair informações estruturadas de textos de extratos bancários e documentos contábeis.

REGRAS IMPORTANTES:
1. Extraia APENAS informações que estão explicitamente no texto
2. Se uma informação não estiver clara, use null
3. O campo "confianca" deve refletir sua certeza geral sobre a extração (0.0 a 1.0)
4. Para CNPJ, mantenha o formato encontrado ou extraia apenas números
5. NÃO extraia datas - o sistema usará automaticamente o mês anterior
6. Foque no cabeçalho do documento: geralmente ele traz NOME, AGÊNCIA e CONTA
7. "Agência" e "Conta" podem aparecer na MESMA linha (ex: "Agência 5684 Conta 001074-0")
8. Preserve o formato original de agência/conta (com hífens) quando houver

CLASSIFICAÇÃO DE TIPO DE DOCUMENTO (Campo: tipo_documento):
Você DEVE classificar o documento em EXATAMENTE UMA das categorias abaixo. Use APENAS estes textos:

TIPOS PRINCIPAIS:
- "EXTRATO DE CONTA CORRENTE" -> Para extratos de conta corrente, movimentação bancária
- "EXTRATO DA CONTA CAPITAL" -> Para extratos de conta capital, capital social
- "EXTRATO CONTA POUPANÇA" -> Para extratos de poupança
- "EXTRATO APLICAÇÃO" -> Para saldo de aplicação, investimentos, CDB, fundos
- "EXTRATO CONSOLIDADO RENDA FIXA" -> Para extratos consolidados de investimentos
- "EXTRATO DE FATURA DE CARTÃO DE CRÉDITO" -> Para faturas de cartão de crédito, maquininhas
- "REL RECEBIMENTO" -> Para relatórios de títulos por período, recebimentos, títulos cadastrados
- "CONTA GRÁFICA DETALHADA" -> Para documentos de conta gráfica detalhada
- "CONTA GRÁFICA SIMPLIFICADA" -> Para documentos de conta gráfica simplificada
- "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS" -> Para relatorios PAR do SICOOB com parcelas liquidadas
- "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO" -> Para relatorios PAR do SICOOB com parcelas em aberto

CÓDIGOS CURTOS (use quando apropriado):
- "CC" -> Conta Corrente (alternativa curta)
- "POUPANÇA" -> Poupança (alternativa curta)
- "CARTÃO" -> Cartão de Crédito (alternativa curta)

OUTROS TIPOS:
- "EXTRATO PIX" -> Para extratos de transferências PIX
- "EXTRATO EMPRÉSTIMO" -> Para empréstimos, financiamentos
- "EXTRATO CONSÓRCIO" -> Para consórcios
- "OUTROS" -> Se não se encaixar em nenhuma categoria acima

IDENTIFICAÇÃO DE BANCOS - INSTRUÇÕES CRÍTICAS:

⚠️ **ATENÇÃO MÁXIMA**: SICOOB e CRESOL são bancos DIFERENTES! NUNCA confunda!

**SICOOB** (Sistema de Cooperativas de Crédito do Brasil):
- Palavras-chave: "SICOOB", "SISBR", "SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL"
- Site: www.sicoob.com.br
- Se você encontrar QUALQUER uma dessas palavras, é SICOOB (NÃO É CRESOL!)

**CRESOL** (Cooperativa de Crédito Rural):
- Palavra-chave: "CRESOL" (SEM "SICOOB" no texto)
- É uma cooperativa diferente do SICOOB

**Outros Bancos**:
- "COOP DE CRED POUP INV SOMA" ou "SICREDI" -> Banco: SICREDI
- "CAIXA ECONOMICA" ou "CAIXA" -> Banco: CAIXA
- "BANCO DO BRASIL" ou "BB S.A" ou "BB" -> Banco: BANCO DO BRASIL
- "BRADESCO" -> Banco: BRADESCO
- "ITAU" ou "ITAÚ" -> Banco: ITAU
- "SANTANDER" -> Banco: SANTANDER

**REGRA DE OURO**:
1. Procure "SICOOB" ou "SISBR" no texto → É SICOOB (nunca CRESOL!)
2. Procure "CRESOL" (sem SICOOB) → É CRESOL
3. Sempre retorne o nome SIMPLIFICADO em UPPERCASE
4. Se não tiver 100% de certeza, retorne null

EXTRAÇÃO DE AGÊNCIA E CONTA - ONDE PROCURAR:

**LOCALIZAÇÃO**: Procure no CABEÇALHO (primeiras 20-30 linhas do documento)

**Padrões comuns**:
1. Linha separada: "Cooperativa: 3037" e "Conta: 75.662-8"
2. Mesma linha: "Agência 5684 Conta 001074-0"
3. Formato tabela:
   ```
   Cooperativa:  3037
   Conta:        75.662-8
   Nome:         COMERCIAL SUL BRASIL LTDA
   CNPJ:         82.697.137/0001-01
   ```

**REGRAS CRÍTICAS**:
- agencia: Extraia o número EXATAMENTE como aparece após "Agência", "Cooperativa" ou "Ag."
  Exemplos: "3037", "5684", "3037-6" (preserve hífens se houver)

- conta: Extraia o número COMPLETO após "Conta", "Conta Corrente" ou "Cc"
  Exemplos: "75.662-8" (com pontos e hífen), "001074-0", "12345-6"
  IMPORTANTE: Inclua TODOS os dígitos, inclusive o verificador (último número após hífen)

  ⚠️ **ATENÇÃO CRÍTICA**: "MATRÍCULA" NÃO É "CONTA"!
  - Se o documento tem "MATRÍCULA" mas NÃO tem campo "Conta", retorne conta=null
  - NUNCA use o número da MATRÍCULA como conta
  - Exemplo: "MATRÍCULA: 1038907" → conta=null (não use 1038907)

**CASOS ESPECIAIS**:

1. **SICOOB - Extrato Consolidado Renda Fixa**:
   - Procure por "Cooperativa:" seguido de número (ex: "Cooperativa: 3037")
   - Procure por "Conta:" seguido de número (ex: "Conta: 75.662-8")
   - Nome geralmente está na linha "Nome:"

2. **Banco do Brasil**:
   - "Conta corrente 20000-X SUPERMERCADO" -> conta="20000-X"
   - Texto após o número da conta é o cliente_sugerido

3. **SICOOB - Extrato da Conta Capital**:
   - ⚠️ **ATENÇÃO**: Este tipo de extrato NÃO possui campo "Conta"
   - Existe apenas "MATRÍCULA", mas MATRÍCULA ≠ CONTA
   - Se o documento for "EXTRATO DA CONTA CAPITAL", retorne conta=null
   - Extraia a COOPERATIVA como agencia e o nome após MATRÍCULA como cliente_sugerido
   - Exemplo: "MATRÍCULA: 1038907 - EMPRESA X" → cliente_sugerido="EMPRESA X", conta=null

EXTRAÇÃO DE CLIENTE (cliente_sugerido):
- Procure por "Nome:", "Razão Social:", ou linha após Conta/Agência
- REMOVA prefixos: "ASSOCIADO:", "CLIENTE:", números de matrícula
- Exemplo: "ASSOCIADO: 123 - EMPRESA X" -> retorne apenas "EMPRESA X"
- NÃO use o nome do banco como cliente
- Para documentos "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS ...":
  - O cliente aparece na tabela sob o rótulo "Cedente"
  - IGNORE "Sacado" (pode conter muitos nomes diferentes)
  - Remova códigos numéricos antes do nome (ex: "54544-9 RODAIR TRATORES E" -> "RODAIR TRATORES E")

EXEMPLOS PRÁTICOS DE EXTRAÇÃO:

**Exemplo 1 - SICOOB Extrato Consolidado Renda Fixa (NÃO É CRESOL!)**:
Texto:
```
SICOOB
SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
SISBR - SISTEMA DE INFORMÁTICA DO SICOOB

EXTRATO CONSOLIDADO RENDA FIXA

Cooperativa:  3037
Conta:        75.662-8
Nome:         COMERCIAL SUL BRASIL LTDA
CNPJ:         82.697.137/0001-01
```

⚠️ **ATENÇÃO**: Veja "SICOOB" e "SISBR" no cabeçalho → É SICOOB (NÃO CRESOL!)

Extração correta:
```json
{
  "cliente_sugerido": "COMERCIAL SUL BRASIL LTDA",
  "cnpj": "82.697.137/0001-01",
  "banco": "SICOOB",
  "agencia": "3037",
  "conta": "75.662-8",
  "contrato": null,
  "tipo_documento": "EXTRATO CONSOLIDADO RENDA FIXA",
  "confianca": 0.95
}
```

❌ **ERRADO**: Retornar "banco": "CRESOL" seria um ERRO GRAVE!

**Exemplo 2 - Banco do Brasil**:
Texto:
```
BANCO DO BRASIL S.A.
Cliente - Conta atual
Agência: 5684
Conta corrente 20000-6 SUPERMERCADO MARTELLI LTDA
```

Extração correta:
```json
{
  "cliente_sugerido": "SUPERMERCADO MARTELLI LTDA",
  "cnpj": null,
  "banco": "BANCO DO BRASIL",
  "agencia": "5684",
  "conta": "20000-6",
  "contrato": null,
  "tipo_documento": "EXTRATO DE CONTA CORRENTE",
  "confianca": 0.9
}
```

FORMATO DE RESPOSTA:
Retorne APENAS um JSON válido, sem explicações adicionais:

{
    "cliente_sugerido": "string ou null",
    "cnpj": "string ou null",
    "banco": "string ou null - UPPERCASE",
    "agencia": "string ou null",
    "conta": "string ou null - COMPLETA com dígito verificador",
    "contrato": "string ou null",
    "tipo_documento": "string - OBRIGATÓRIO",
    "confianca": "number - 0.0 a 1.0"
}"""

# Prompt especifico para arquivos OFX
OFX_SYSTEM_PROMPT = """Você é um especialista em extração de dados de arquivos OFX (Open Financial Exchange).
Seu objetivo é extrair com precisão: banco, agência, conta e tipo de documento.

## REGRAS FUNDAMENTAIS
1. Extraia APENAS dados explicitamente presentes no OFX
2. NÃO invente, deduza ou infira informações
3. Se um dado não existir, retorne null
4. Preserve o formato original de agência/conta (incluindo hífens)
5. NÃO extraia datas (o sistema controla isso)

## TAGS OFX E COMO EXTRAIR

### Tags SGML vs XML
Arquivos OFX podem usar dois formatos:
- **XML**: `<BRANCHID>5684</BRANCHID>` (tag com fechamento)
- **SGML**: `<BRANCHID>5684` (tag SEM fechamento)

AMBOS os formatos são válidos. O valor da tag vem IMEDIATAMENTE após o nome da tag.

### Agência (campo: agencia)
**Passo 1**: Procure pela tag `<BRANCHID>`
- Se encontrar: use o valor IMEDIATAMENTE após `<BRANCHID>`
- Exemplo XML: `<BRANCHID>3037-6</BRANCHID>` → agencia = "3037-6"
- Exemplo SGML: `<BRANCHID>5684` → agencia = "5684"

**Passo 2**: Se NÃO encontrar `<BRANCHID>`, verifique se é SICREDI:
- Procure `<ORG>COOP DE CRED POUP INV SOMA PR` no OFX
- Se for SICREDI E `<ACCTID>` tiver 10+ dígitos:
  - Os 3 ou 4 PRIMEIROS dígitos do `<ACCTID>` são a agência
  - Exemplo: `<ACCTID>7370000000594105</ACCTID>` → agencia = "737"

**Passo 3**: Se ainda não encontrou, retorne null

### Conta (campo: conta)
**Passo 1**: Procure pela tag `<ACCTID>`
- Se encontrar: use o valor IMEDIATAMENTE após `<ACCTID>`
- Exemplo XML: `<ACCTID>45841-4</ACCTID>` → conta = "45841-4"
- Exemplo SGML: `<ACCTID>005908` → conta = "005908"

**Passo 2**: Se for SICREDI com `<ACCTID>` longo (10+ dígitos):
- Remova os 3 ou 4 primeiros dígitos (que são a agência)
- Use o restante como conta
- Exemplo: `<ACCTID>7370000000594105</ACCTID>` → conta = "0000000594105"

**Passo 3**: Se não encontrou `<ACCTID>`, retorne null

### Banco (campo: banco)
Procure pela tag `<ORG>` dentro de `<FI>`:
- "Banco Cooperativo do Brasil" → "SICOOB"
- "COOP DE CRED POUP INV SOMA PR" → "SICREDI"
- "SICOOB" → "SICOOB"
- "CRESOL" → "CRESOL"
- "Banco do Brasil S/A" ? "BANCO DO BRASIL"
- Sempre retorne em UPPERCASE

### Tipo de Documento (campo: tipo_documento)
Identifique pelo tipo de extrato COM PRIORIDADE:

Regras para identificar CONTA CAPITAL:
- Se existir <MEMO> com texto "SUBSCRICAO DE CAPITAL" (ou apenas "SUBSCRICAO"), classifique como "EXTRATO DA CONTA CAPITAL".
- Se existir <MEMO> com "SALDO ANTERIOR" e o total de <STMTTRN> for pequeno (ex.: menor que 10), classifique como "EXTRATO DA CONTA CAPITAL".
- Essas regras tem prioridade sobre conta corrente.


**ATENÇÃO: Verifique PRIMEIRO se é Conta Capital antes de classificar como Conta Corrente!**

1. Se tiver `<MEMO>SUBSCRICAO DE CAPITAL</MEMO>` OU "SUBSCRICAO" no texto → **"EXTRATO DA CONTA CAPITAL"**
2. Se tiver `<MEMO>SALDO ANTERIOR</MEMO>` E total de transações < 10 → **"EXTRATO DA CONTA CAPITAL"**
3. Se tiver `<BANKTRANLIST>` sem indicação de capital → "EXTRATO DE CONTA CORRENTE"
4. Se tiver `<SAVSTMTTRNRS>` → "EXTRATO CONTA POUPANCA"
5. Se tiver `<INVSTMTTRNRS>` → "EXTRATO APLICACAO"
6. Se tiver `<CCSTMTTRNRS>` → "EXTRATO DE FATURA DE CARTAO DE CREDITO"

## EXEMPLOS PRÁTICOS (Few-Shot)

### Exemplo 1: XML com tags completas (Banco SICOOB)
```
<ORG>Banco Cooperativo do Brasil</ORG>
<BRANCHID>3037-6</BRANCHID>
<ACCTID>45841-4</ACCTID>
<BANKTRANLIST>
```
**Extração**:
- banco: "SICOOB"
- agencia: "3037-6"
- conta: "45841-4"
- tipo_documento: "EXTRATO DE CONTA CORRENTE"

### Exemplo 2: SGML sem fechamento de tags
```
<BRANCHID>5684
<ACCTID>005908
<BANKTRANLIST>
```
**Extração**:
- banco: null (não tem <ORG> visível)
- agencia: "5684"
- conta: "005908"
- tipo_documento: "EXTRATO DE CONTA CORRENTE"

### Exemplo 3: SICREDI sem BRANCHID
```
<ORG>COOP DE CRED POUP INV SOMA PR/</ORG>
<ACCTID>7370000000594105</ACCTID>
<BANKTRANLIST>
```
**Extração**:
- banco: "SICREDI"
- agencia: "737" (primeiros 3 dígitos do ACCTID)
- conta: "0000000594105" (restante do ACCTID)
- tipo_documento: "EXTRATO DE CONTA CORRENTE"

### Exemplo 4: Conta Capital (Banco SICOOB)
```
<ORG>Banco Cooperativo do Brasil</ORG>
<BRANCHID>3037</BRANCHID>
<ACCTID>290-6</ACCTID>
<MEMO>SUBSCRICAO DE CAPITAL</MEMO>
```
**Extração**:
- banco: "SICOOB"
- agencia: "3037"
- conta: "290-6"
- tipo_documento: "EXTRATO DA CONTA CAPITAL"

## FORMATO DE SAÍDA
Retorne APENAS JSON válido:
{
  "cliente_sugerido": null,
  "cnpj": null,
  "banco": "string ou null",
  "agencia": "string ou null",
  "conta": "string ou null",
  "contrato": null,
  "tipo_documento": "string",
  "confianca": 0.9
}

Se extraiu agencia e conta com sucesso, use confianca >= 0.85"""

class LLMService:
    """Serviço de extração de informações usando LLM."""
    
    def __init__(self):
        """Inicializa o serviço com as configurações."""
        settings = get_settings()
        
        self.llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            temperature=0,  # Respostas mais determinísticas
            max_tokens=1000,
        )

        self.parser = JsonOutputParser()

        # Cache de extração por hash de arquivo
        self._extraction_cache: dict[str, LLMExtractionResult] = {}
        self._extraction_cache_lock = threading.Lock()
        self._extraction_cache_max = 200
        self._extraction_cache_hits = 0
        self._extraction_cache_misses = 0

    def _compute_file_hash(self, pdf_data: bytes | None, text: str) -> str:
        """Computa MD5 do conteúdo do arquivo para uso como chave de cache."""
        content = pdf_data if pdf_data else text.encode("utf-8", errors="replace")
        return hashlib.md5(content).hexdigest()

    def _get_cached_extraction(self, file_hash: str) -> LLMExtractionResult | None:
        """Retorna resultado cacheado para o hash, ou None se não estiver no cache."""
        with self._extraction_cache_lock:
            cached = self._extraction_cache.get(file_hash)
            if cached is not None:
                self._extraction_cache_hits += 1
                logger.info(
                    "[EXTRACTION_CACHE] hit | hash=%s | tipo=%s confianca=%.2f",
                    file_hash[:8],
                    cached.tipo_documento,
                    cached.confianca,
                )
                return cached.model_copy()
            self._extraction_cache_misses += 1
            return None

    def _store_cached_extraction(self, file_hash: str, result: LLMExtractionResult) -> None:
        """Armazena resultado no cache, evictando o mais antigo se atingir limite."""
        with self._extraction_cache_lock:
            if len(self._extraction_cache) >= self._extraction_cache_max:
                oldest_key = next(iter(self._extraction_cache))
                del self._extraction_cache[oldest_key]
            self._extraction_cache[file_hash] = result.model_copy()

    def get_extraction_cache_stats(self) -> dict:
        """Retorna estatísticas do cache de extração."""
        with self._extraction_cache_lock:
            total = self._extraction_cache_hits + self._extraction_cache_misses
            hit_rate = self._extraction_cache_hits / total if total > 0 else 0.0
            return {
                "size": len(self._extraction_cache),
                "max_size": self._extraction_cache_max,
                "hits": self._extraction_cache_hits,
                "misses": self._extraction_cache_misses,
                "hit_rate": round(hit_rate, 3),
            }

    def clear_extraction_cache(self) -> None:
        """Limpa o cache de extração e reseta os contadores."""
        with self._extraction_cache_lock:
            self._extraction_cache.clear()
            self._extraction_cache_hits = 0
            self._extraction_cache_misses = 0
        logger.info("[EXTRACTION_CACHE] cache limpo")

    def _extract_header_chunk(self, text: str) -> str:
        """Extrai um chunk inicial do documento, priorizando o cabecalho."""
        if not text:
            return ""

        if RecursiveCharacterTextSplitter is None:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            header = "\n".join(lines[:12]).strip()
            return header[:500] if header else text[:500]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=0,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_text(text)
        if not chunks:
            return text[:500]
        return chunks[0]

    def _classify_from_keywords(self, text: str) -> str | None:
        """Classifica tipo por keywords priorizadas."""
        normalized = self._normalize_text_for_hint(text)
        for tipo, keywords in TIPO_KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                return tipo
        return None

    def _classify_from_fuzzy(
        self,
        text: str,
        threshold: int = 80,
    ) -> tuple[str, float] | None:
        """Classifica tipo por similaridade fuzzy contra os tipos canonicos."""
        normalized = self._normalize_text_for_hint(text)
        if not normalized:
            return None

        result = process.extractOne(
            normalized,
            TIPOS_CANONICOS,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )
        if result is None:
            return None

        tipo, score, _ = result
        return tipo, round(score / 100, 2)

    def _normalize_tipo_documento(self, tipo: str | None) -> str | None:
        """Normaliza variacoes do tipo retornado para o valor canonico."""
        if not tipo:
            return tipo

        normalized = self._normalize_text_for_hint(tipo)
        if not normalized:
            return tipo

        if normalized in TIPO_ALIASES:
            return TIPO_ALIASES[normalized]
        if normalized in TIPOS_CANONICOS:
            return normalized
        return tipo.strip().upper()

    # Termos que indicam linhas com campos relevantes para extração
    _RELEVANT_KEYWORDS = [
        # Identificação do cliente
        "cliente", "associado", "beneficiário", "beneficiario", "titular",
        "nome", "razão social", "razao social", "empresa",
        "cpf", "cnpj", "cpf/cnpj",
        # Dados bancários
        "banco", "instituição", "instituicao", "cooperativa", "coop",
        "agência", "agencia", "ag.", "ag ",
        "conta", "c/c", "cc:", "conta corrente", "conta capital",
        "conta poupança", "conta poupanca",
        # Tipo de documento
        "extrato", "relatório", "relatorio", "demonstrativo",
        "boleto", "empréstimo", "emprestimo", "financiamento",
        "contrato", "apólice", "apolice",
        # Período
        "período", "periodo", "competência", "competencia",
        "referência", "referencia", "data", "mês", "mes", "ano",
        "de:", "até:", "ate:", "vigência", "vigencia",
        # Matrícula (Conta Capital)
        "matrícula", "matricula",
    ]

    def _preprocess_text_for_llm(self, text: str) -> str:
        """
        Pré-processa o texto extraído para destacar campos relevantes.

        Varre as linhas procurando termos-chave (banco, conta, cliente, etc.)
        e monta um bloco <campos_relevantes> no topo, seguido do texto completo.
        Isso reduz o ruído de formatação e orienta a atenção da LLM.
        """
        lines = text.splitlines()
        relevant_lines = []
        seen = set()

        keywords_lower = [k.lower() for k in self._RELEVANT_KEYWORDS]

        for line in lines:
            stripped = line.strip()
            if not stripped or len(stripped) < 4:
                continue
            line_lower = stripped.lower()
            if any(kw in line_lower for kw in keywords_lower):
                # Evita duplicatas (mesma linha aparecendo mais de uma vez)
                key = line_lower[:80]
                if key not in seen:
                    seen.add(key)
                    relevant_lines.append(stripped)
            # Limita a 25 linhas para não inflar o bloco
            if len(relevant_lines) >= 25:
                break

        if not relevant_lines:
            return text

        campos_block = "\n".join(f"  {l}" for l in relevant_lines)
        return (
            f"<campos_relevantes>\n{campos_block}\n</campos_relevantes>\n\n"
            f"<texto_completo>\n{text}\n</texto_completo>"
        )

    def _build_human_message(self, text: str, tipo_hint: str | None = None) -> str:
        """Monta a mensagem enviada para a LLM com hint opcional."""
        hint_block = ""
        if tipo_hint:
            hint_block = (
                "\n\n<dica_pre_analise>"
                f"\nAnalise textual preliminar sugere: tipo_documento = \"{tipo_hint}\""
                "\nConfirme ou corrija com base no texto completo."
                "\n</dica_pre_analise>"
            )
        structured_text = self._preprocess_text_for_llm(text)
        return f"TEXTO DO DOCUMENTO:{hint_block}\n\n{structured_text}"

    def _get_tipo_analysis_text(self, text: str, is_pdf: bool = False) -> str:
        """
        Define o recorte usado para classificar tipo.

        Para PDF, prioriza apenas o inicio do documento, onde o cabecalho
        normalmente ja informa o tipo e evita ruido do corpo.
        """
        if not text:
            return ""
        if not is_pdf:
            return text
        return text[:3500]

    def _apply_tipo_classification_pipeline(
        self,
        result: LLMExtractionResult,
        text: str,
    ) -> LLMExtractionResult:
        """Aplica heuristicas deterministicas antes e depois da LLM."""
        result.tipo_documento = self._normalize_tipo_documento(result.tipo_documento)

        header_chunk = self._extract_header_chunk(text)
        header_keyword = self._classify_from_keywords(header_chunk)
        if header_keyword:
            if result.tipo_documento != header_keyword:
                logger.info(
                    "Tipo ajustado por keyword no cabecalho: %s -> %s",
                    result.tipo_documento,
                    header_keyword,
                )
            result.tipo_documento = header_keyword
            result.confianca = max(result.confianca, 0.95)
            return result

        current_tipo = self._normalize_tipo_documento(result.tipo_documento)
        generic_or_low_confidence = current_tipo in (
            None,
            "OUTROS",
            "EXTRATO DE CONTA CORRENTE",
        ) or result.confianca < 0.82

        if generic_or_low_confidence:
            fuzzy_header = self._classify_from_fuzzy(header_chunk, threshold=82)
            if fuzzy_header:
                tipo_fuzzy, conf_fuzzy = fuzzy_header
                logger.info(
                    "Tipo ajustado por fuzzy no cabecalho: %s -> %s (score=%.0f%%)",
                    result.tipo_documento,
                    tipo_fuzzy,
                    conf_fuzzy * 100,
                )
                result.tipo_documento = tipo_fuzzy
                result.confianca = max(result.confianca, round(conf_fuzzy * 0.9, 2))
                return result

        if (
            result.confianca < 0.70
            and current_tipo in (None, "OUTROS", "EXTRATO DE CONTA CORRENTE")
            and len(text) > 500
        ):
            tipo_fallback = self._classify_from_keywords(text)
            if tipo_fallback and tipo_fallback != current_tipo:
                logger.info(
                    "Tipo ajustado por segunda passagem de keywords: %s -> %s",
                    result.tipo_documento,
                    tipo_fallback,
                )
                result.tipo_documento = tipo_fallback
                result.confianca = max(result.confianca, 0.75)
                return result

            fuzzy_full = self._classify_from_fuzzy(text, threshold=78)
            if fuzzy_full:
                tipo_fuzzy, conf_fuzzy = fuzzy_full
                logger.info(
                    "Tipo ajustado por segunda passagem fuzzy: %s -> %s (score=%.0f%%)",
                    result.tipo_documento,
                    tipo_fuzzy,
                    conf_fuzzy * 100,
                )
                result.tipo_documento = tipo_fuzzy
                result.confianca = max(result.confianca, round(conf_fuzzy * 0.85, 2))

        return result

    def _normalize_account_field(self, value: str) -> str:
        """
        Remove pontos, hifens e outros caracteres não numéricos.
        Remove zeros à esquerda. Mantém apenas dígitos.

        Args:
            value: Valor original (ex: "3037-6", "005908", "0000000594105")

        Returns:
            Valor normalizado sem zeros à esquerda (ex: "30376", "5908", "594105")
        """
        if not value:
            return value
        # Remove caracteres não numéricos
        value = re.sub(r"(?i)(\d)\s*-\s*x\b", r"\g<1>-0", value.strip())
        value = re.sub(r"(?i)(\d)\s*x\b", r"\g<1>0", value)
        only_numbers = re.sub(r'[^0-9]', '', value)
        # Remove zeros à esquerda (mas mantém "0" se for só zeros)
        return only_numbers

    def extract_info(self, text: str) -> LLMExtractionResult:
        """
        Extrai informações estruturadas do texto do documento.
        
        Utiliza a LLM para analisar o texto e retornar um JSON
        com as informações identificadas.
        
        Args:
            text: Texto extraído do documento PDF
            
        Returns:
            Resultado da extração com todas as informações
            
        Raises:
            Exception: Se houver erro na chamada da LLM ou parsing
        """
        # Limita o texto para evitar exceder o contexto
        # Geralmente as informações importantes estão no início
        max_chars = 15000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[TEXTO TRUNCADO...]"

        header_chunk = self._extract_header_chunk(text)
        tipo_hint = self._classify_from_keywords(header_chunk)
        
        # Monta as mensagens para a LLM
        messages = [
            SystemMessage(content=self._select_system_prompt(text)),
            HumanMessage(content=self._build_human_message(text, tipo_hint=tipo_hint)),
        ]
        
        try:
            # Chama a LLM
            response = self.llm.invoke(messages)
            
            # Extrai o conteúdo da resposta
            content = response.content.strip()
            
            # Remove possíveis marcadores de código
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            # Parse do JSON
            data = json.loads(content.strip())
            
            # Valida e converte para o schema Pydantic
            result = LLMExtractionResult(**data)
            result.tipo_documento = self._normalize_tipo_documento(result.tipo_documento)

            if tipo_hint:
                result.tipo_documento = tipo_hint
                result.confianca = max(result.confianca, 0.95)

            # Normaliza agência e conta (remove pontos, hifens, mantém apenas números)
            if result.agencia:
                result.agencia = self._normalize_account_field(result.agencia)
            if result.conta:
                result.conta = self._normalize_account_field(result.conta)

            logger.info(
                f"Extração LLM concluída: cliente={result.cliente_sugerido}, "
                f"banco={result.banco}, confianca={result.confianca}"
            )

            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao fazer parse do JSON da LLM: {e}")
            logger.debug(f"Resposta da LLM: {response.content}")
            raise ValueError(f"Resposta da LLM não é um JSON válido: {e}")
            
        except Exception as e:
            logger.error(f"Erro na chamada da LLM: {e}")
            raise
    
    def extract_info_with_fallback(self, text: str, pdf_data: bytes | None = None) -> LLMExtractionResult:
        """
        Extrai informações com fallback para valores padrão e visão.

        Se a extração falhar, retorna um resultado com valores padrão.
        Se o banco não for identificado, tenta usar visão computacional.

        Args:
            text: Texto extraído do documento PDF
            pdf_data: Bytes do PDF (opcional) para fallback de visão

        Returns:
            Resultado da extração (real ou fallback)
        """
        try:
            # Verifica cache antes de chamar a LLM
            file_hash = self._compute_file_hash(pdf_data, text or "")
            cached = self._get_cached_extraction(file_hash)
            if cached is not None:
                return cached

            analysis_text = text or ""
            tipo_analysis_text = self._get_tipo_analysis_text(
                analysis_text,
                is_pdf=pdf_data is not None and not self._is_ofx_text(analysis_text),
            )
            if pdf_data and self._needs_header_ocr(analysis_text):
                header_text = self._try_extract_header_text(pdf_data)
                if header_text:
                    analysis_text = f"{header_text}\n\n{analysis_text}"
                    tipo_analysis_text = self._get_tipo_analysis_text(
                        analysis_text,
                        is_pdf=not self._is_ofx_text(analysis_text),
                    )

            result = self.extract_info(analysis_text)

            # Captura tipo e confiança da LLM antes do pipeline (para auditoria)
            tipo_llm = result.tipo_documento
            confianca_llm = result.confianca

            result = self._apply_tipo_classification_pipeline(result, tipo_analysis_text)

            # Heurística textual: confirma banco por pistas fortes no texto
            banco_hint = self._infer_bank_from_text_hints(analysis_text)
            if banco_hint:
                if not result.banco or result.banco == "null" or result.banco != banco_hint:
                    logger.info(f"Banco ajustado por pista textual: {banco_hint}")
                result.banco = banco_hint
                if result.confianca < 0.85:
                    result.confianca = 0.85

            # Heuristica textual: conta capital -> MATRICULA NÃO É CONTA!
            if self._is_conta_capital(analysis_text, result.tipo_documento):
                # IMPORTANTE: MATRÍCULA NÃO É CONTA!
                # Extrato da Conta Capital não tem campo "Conta", então conta deve ser None
                if result.conta:
                    # Verifica se a conta extraída é na verdade uma matrícula
                    conta_capital = self._extract_conta_capital_account(analysis_text)
                    if conta_capital:
                        conta_numbers = extract_numbers(result.conta) if result.conta else ""
                        capital_numbers = extract_numbers(conta_capital)
                        if conta_numbers == capital_numbers:
                            logger.info(f"Removendo matrícula {conta_capital} do campo conta (MATRÍCULA ≠ CONTA)")
                            result.conta = None
                else:
                    # Se nao ha conta explicita, nao forcar numero
                    if not self._has_explicit_conta_number(analysis_text):
                        result.conta = None
            # Heuristica textual: contrato de emprestimo
            if self._is_emprestimo(analysis_text, result.tipo_documento):
                contrato = self._extract_contract_number(analysis_text)
                if contrato and not result.contrato:
                    logger.info(f"Contrato identificado: {contrato}")
                    result.contrato = contrato
                    if result.confianca < 0.85:
                        result.confianca = 0.85
                if not result.contrato and pdf_data:
                    try:
                        from app.services.vision_service import VisionService
                        vision_service = VisionService()
                        contrato_ocr = vision_service.extract_contract_number_from_pdf(pdf_data)
                        if contrato_ocr:
                            contrato_clean = self._normalize_contract_number(contrato_ocr)
                            if contrato_clean:
                                logger.info(f"Contrato identificado por OCR: {contrato_clean}")
                                result.contrato = contrato_clean
                                if result.confianca < 0.85:
                                    result.confianca = 0.85
                    except Exception as e:
                        logger.warning(f"Erro ao tentar identificar contrato por OCR: {e}")


            # Heurística textual: RENDE FACIL indica extrato de investimento
            if (
                self._has_rende_facil_hint(tipo_analysis_text)
                and result.tipo_documento not in ("EXTRATO DE CONTA CORRENTE",)
            ):
                result.tipo_documento = "EXTRATO APLICACAO"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heuristica textual: relatorio PAR do SICOOB (parcelas em aberto/liquidadas)
            par_tipo = self._detect_par_report_type(tipo_analysis_text)
            if par_tipo:
                result.tipo_documento = par_tipo
                if result.confianca < 0.85:
                    result.confianca = 0.85

            result = self._apply_tipo_classification_pipeline(result, tipo_analysis_text)

            # Se o banco não foi identificado, tenta OCR (visão) no PDF
            if (not result.banco or result.banco == "null") and pdf_data:
                logger.info("Banco não identificado no texto, tentando OCR...")
                try:
                    from app.services.vision_service import VisionService
                    vision_service = VisionService()
                    banco_visual = vision_service.identify_bank_from_ocr(pdf_data)
                    if banco_visual:
                        logger.info(f"Banco identificado por OCR: {banco_visual}")
                        result.banco = banco_visual
                        if result.confianca < 0.9:
                            result.confianca = 0.9
                except Exception as e:
                    logger.warning(f"Erro ao tentar identificar banco por OCR: {e}")

            # Se ainda não identificou banco, tenta visão por logo/imagens
            if (not result.banco or result.banco == "null") and pdf_data:
                try:
                    from app.services.pdf_service import PDFService
                    pdf_service = PDFService()
                    images = pdf_service.extract_first_page_images(pdf_data)
                    if images:
                        banco_visual = self.identify_bank_from_images(images)
                        if banco_visual:
                            logger.info(f"Banco identificado por visao (logos): {banco_visual}")
                            result.banco = banco_visual
                            if result.confianca < 0.9:
                                result.confianca = 0.9
                except Exception as e:
                    logger.warning(f"Erro ao tentar identificar banco por logos: {e}")

            # Se ainda não identificou banco, tenta inferir pela planilha de clientes (agência/conta)
            if (not result.banco or result.banco == "null") and result.agencia and result.conta:
                banco_planilha = self._infer_bank_from_clients(result.agencia, result.conta)
                if banco_planilha:
                    logger.info(f"Banco inferido pela planilha de clientes: {banco_planilha}")
                    result.banco = banco_planilha
                    if result.confianca < 0.8:
                        result.confianca = 0.8

            # Log de auditoria: tipo LLM vs tipo final após todo o pipeline
            if tipo_llm != result.tipo_documento:
                logger.info(
                    "[TIPO_AUDIT] corrigido | LLM='%s' (%.2f) → FINAL='%s' (%.2f)",
                    tipo_llm,
                    confianca_llm,
                    result.tipo_documento,
                    result.confianca,
                )
            else:
                logger.debug(
                    "[TIPO_AUDIT] mantido   | LLM='%s' (%.2f) → FINAL='%s' (%.2f)",
                    tipo_llm,
                    confianca_llm,
                    result.tipo_documento,
                    result.confianca,
                )

            # Armazena no cache apenas resultados com confiança mínima aceitável
            if result.confianca >= 0.5:
                self._store_cached_extraction(file_hash, result)
                logger.debug(
                    "[EXTRACTION_CACHE] stored | hash=%s | tipo=%s confianca=%.2f",
                    file_hash[:8],
                    result.tipo_documento,
                    result.confianca,
                )

            return result

        except Exception as e:
            logger.warning(f"Usando fallback devido a erro: {e}")

            return LLMExtractionResult(
                cliente_sugerido=None,
                cnpj=None,
                banco=None,
                agencia=None,
                conta=None,
                contrato=None,
                tipo_documento="OUTROS",
                confianca=0.0,
            )

    def _select_system_prompt(self, text: str) -> str:
        """Seleciona o prompt do sistema baseado no tipo de conteudo."""
        return OFX_SYSTEM_PROMPT if self._is_ofx_text(text) else SYSTEM_PROMPT

    def _is_ofx_text(self, text: str) -> bool:
        """Heuristica simples para detectar arquivos OFX pelo conteudo."""
        if not text:
            return False
        sample = text[:2000].upper()
        if "OFXHEADER" in sample or "<OFX" in sample:
            return True
        if "<BANKTRANLIST>" in sample or "<STMTRS>" in sample:
            return True
        if "<CCSTMTTRNRS>" in sample or "<INVSTMTTRNRS>" in sample:
            return True
        return False

    def _infer_bank_from_clients(self, agencia: str, conta: str) -> str | None:
        """Infere o banco usando a planilha de clientes (agência + conta)."""
        def _normalize_number(value: str) -> str:
            numbers = extract_numbers(value)
            # Remove zeros à esquerda para evitar mismatch por formatação
            return numbers.lstrip("0") or "0"

        def _match_agencia(agencia_ofx: str, agencia_planilha: str) -> bool:
            """Verifica se agências são equivalentes, ignorando dígito verificador."""
            norm_ofx = _normalize_number(agencia_ofx)
            norm_planilha = _normalize_number(agencia_planilha)

            # Match exato
            if norm_ofx == norm_planilha:
                return True

            # OFX pode não ter dígito verificador (ex: 5684 vs 5684-7)
            # Verifica se um é prefixo do outro (ignorando último dígito)
            if norm_ofx.startswith(norm_planilha) or norm_planilha.startswith(norm_ofx):
                return True

            # Verifica se diferem apenas no último dígito (dígito verificador)
            if len(norm_ofx) == len(norm_planilha) - 1:
                if norm_planilha.startswith(norm_ofx):
                    return True
            if len(norm_planilha) == len(norm_ofx) - 1:
                if norm_ofx.startswith(norm_planilha):
                    return True

            return False

        agencia_numbers = _normalize_number(agencia)
        conta_numbers = _normalize_number(conta)
        if not agencia_numbers or not conta_numbers:
            return None

        try:
            client_service = ClientService()
            clients = client_service.load_clients()
        except Exception as e:
            logger.warning(f"Erro ao carregar clientes para inferir banco: {e}")
            return None

        bancos: set[str] = set()
        for client in clients:
            if not client.agencia or not client.conta or not client.banco:
                continue

            # Usa matching flexível para agência (com/sem dígito verificador)
            if not _match_agencia(agencia, str(client.agencia)):
                continue

            # Usa matching normalizado para conta (ignora zeros e hífens)
            if _normalize_number(str(client.conta)) != conta_numbers:
                continue

            bancos.add(client.banco.strip().upper())

        if len(bancos) == 1:
            return next(iter(bancos))
        if len(bancos) > 1:
            logger.warning(
                "Banco não inferido: múltiplos bancos para agência/conta %s/%s: %s",
                agencia,
                conta,
                ", ".join(sorted(bancos)),
            )
        return None

    def _normalize_text_for_hint(self, text: str) -> str:
        """Normaliza texto para checagem de pistas."""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^A-Z0-9]+", " ", normalized.upper())
        return " ".join(normalized.split())

    def _infer_bank_from_text_hints(self, text: str) -> str | None:
        """Infere banco com base em pistas fortes no texto."""
        normalized = self._normalize_text_for_hint(text)
        compact = normalized.replace(" ", "")
        compact_ambiguous = compact.replace("0", "O")

        # SICOOB - pistas MUITO fortes (NUNCA confundir com CRESOL!)
        if "SICOOB" in normalized or "SICOOB" in compact or "SICOOB" in compact_ambiguous:
            return "SICOOB"
        if "SISBR" in normalized:
            return "SICOOB"
        if "SISTEMA DE INFORMATIC DO SICOOB" in normalized:
            return "SICOOB"
        if "SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL" in normalized:
            return "SICOOB"

        # CRESOL - diferente de SICOOB!
        if "CRESOL" in normalized and "SICOOB" not in normalized and "SICOOB" not in compact and "SICOOB" not in compact_ambiguous:
            return "CRESOL"

        # Banco do Brasil - pistas fortes
        if "OUVIDORIA BB 0800 729 5678" in normalized:
            return "BANCO DO BRASIL"
        if "SAC 0800 729 0722" in normalized:
            return "BANCO DO BRASIL"
        if "BANCO DO BRASIL" in normalized or "BANCO DO BRASIL SA" in normalized:
            return "BANCO DO BRASIL"
        if "VISUALIZAR PIX AGRUPADOS" in normalized and "CLIENTE CONTA ATUAL" in normalized:
            return "BANCO DO BRASIL"
        if "CLIENTE CONTA ATUAL" in normalized and "CONTA CORRENTE" in normalized:
            return "BANCO DO BRASIL"

        return None

    def _is_emprestimo(self, text: str, tipo_documento: str | None) -> bool:
        """Detecta extrato de emprestimo pelo tipo ou pelo texto."""
        if tipo_documento and "EMPRESTIMO" in tipo_documento.upper():
            return True
        normalized = self._normalize_text_for_hint(text)
        return ("NUMERO DO CONTRATO" in normalized
                or "OPERACAO DE CREDITO" in normalized
                or "EXTRATO DE OPERACAO DE CREDITO" in normalized)

    def _extract_contract_number(self, text: str) -> str | None:
        """Extrai numero do contrato de emprestimo."""
        if not text:
            return None
        normalized_text = unicodedata.normalize("NFKD", text)
        normalized_text = normalized_text.encode("ascii", "ignore").decode("ascii")
        # Padroes diretos
        match = re.search(
            r"\bNUM(?:ERO|\.)\s*DO\s*CONTRATO\b\s*[:\-]?\s*([0-9][0-9./-]{3,20})",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(
            r"\bN[O0]\s*DO\s*CONTRATO\b\s*[:\-]?\s*([0-9][0-9./-]{3,20})",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(
            r"\bCONTRATO\b\s*[:\-]?\s*([0-9][0-9./-]{3,20})",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        # Fallback: procura na linha que contem "CONTRATO"
        for line in normalized_text.splitlines():
            if "CONTRATO" not in line:
                continue
            numbers = re.findall(r"[0-9]{4,20}", line)
            if numbers:
                # Usa o maior bloco numerico encontrado na linha
                numbers.sort(key=len, reverse=True)
                return numbers[0].strip()

        return None

    def _normalize_contract_number(self, value: str) -> str | None:
        """Normaliza numero do contrato retornado por OCR."""
        if not value:
            return None
        cleaned = value.strip().upper()
        if cleaned == "DESCONHECIDO":
            return None
        # Se vier uma linha completa, tenta extrair ao lado de CONTRATO
        if "CONTRATO" in cleaned:
            for line in cleaned.splitlines():
                if "CONTRATO" not in line:
                    continue
                match = re.search(r"[0-9][0-9./-]{3,20}", line)
                if match:
                    return match.group(0)
        match = re.search(r"[0-9][0-9./-]{3,20}", cleaned)
        if match:
            return match.group(0)
        return None

    def _has_rende_facil_hint(self, text: str) -> bool:
        """Detecta indicio de investimento pelo termo RENDE FACIL."""
        normalized = self._normalize_text_for_hint(text)
        return "RENDE FACIL" in normalized

    def _detect_par_report_type(self, text: str) -> str | None:
        """Detecta relatorio PAR do SICOOB e classifica o tipo."""
        normalized = self._normalize_text_for_hint(text)
        if "PAR RELATORIO SELECAO DE OPERACOES PARCELAS" not in normalized:
            return None
        if "LIQUIDADAS" in normalized:
            return "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS"
        if "EM ABERTO" in normalized or "PARCELAS EM ABERTO" in normalized:
            return "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO"
        return None

    def _is_conta_capital(self, text: str, tipo_documento: str | None) -> bool:
        """Detecta extrato de conta capital pelo tipo ou pelo texto."""
        if tipo_documento and "CONTA CAPITAL" in tipo_documento.upper():
            return True
        normalized = self._normalize_text_for_hint(text)
        if "CONTA CAPITAL" in normalized:
            return True
        if "CAPITAL SOCIAL" in normalized:
            return True
        return False


    def _needs_header_ocr(self, text: str) -> bool:
        """Detecta se o texto parece estar sem cabecalho relevante."""
        normalized = self._normalize_text_for_hint(text)
        if not normalized:
            return True
        markers = [
            "BANCO",
            "SICOOB",
            "SICREDI",
            "CRESOL",
            "BRADESCO",
            "ITAU",
            "SANTANDER",
            "CAIXA",
            "BANCO DO BRASIL",
            "CONTA",
            "AGENCIA",
            "COOPERATIVA",
            "MATRICULA",
            "EXTRATO",
        ]
        return not any(marker in normalized for marker in markers)

    def _try_extract_header_text(self, pdf_data: bytes) -> str | None:
        """Tenta extrair texto do cabecalho via OCR."""
        try:
            from app.services.vision_service import VisionService

            vision_service = VisionService()
            header_text = vision_service.extract_header_text_from_pdf(pdf_data, max_pages=1)
            if header_text:
                logger.info("Texto de cabecalho extraido por OCR.")
                return header_text
        except Exception as e:
            logger.warning(f"Erro ao tentar extrair texto do cabecalho via OCR: {e}")
        return None
    def _infer_sicoob_from_conta_capital(self, text: str) -> str | None:
        """Infere banco SICOOB por pistas fortes em extrato de conta capital."""
        normalized = self._normalize_text_for_hint(text)
        if "SICOOB" in normalized:
            return "SICOOB"
        if "SISBR" in normalized:
            return "SICOOB"
        if "SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL" in normalized:
            return "SICOOB"
        return None

    def _extract_conta_capital_account(self, text: str) -> str | None:
        """Extrai conta de extrato de conta capital (prioriza MATRICULA)."""
        if not text:
            return None
        normalized_text = unicodedata.normalize("NFKD", text)
        normalized_text = normalized_text.encode("ascii", "ignore").decode("ascii")
        matricula_match = re.search(
            r"\bMATRICULA\b\s*[:\-]?\s*([0-9]{1,12}(?:[-/][0-9A-Z]+)?)",
            normalized_text,
            flags=re.IGNORECASE,
        )
        if matricula_match:
            return matricula_match.group(1).strip()

        return None

    def _has_explicit_conta_number(self, text: str) -> bool:
        """Detecta se existe conta explicita no texto."""
        if not text:
            return False
        normalized_text = unicodedata.normalize("NFKD", text)
        normalized_text = normalized_text.encode("ascii", "ignore").decode("ascii")
        return bool(re.search(r"\bCONTA\b\s*[:\-]?\s*[0-9]", normalized_text, flags=re.IGNORECASE))


    def identify_bank_from_images(self, images_base64: list[str]) -> str | None:
        """
        Tenta identificar o banco analisando as imagens (logos) extraídas.
        Usa a capacidade de Visão do modelo.
        """
        if not images_base64:
            return None
            
        try:
            # Prepara mensagens com imagens
            content_parts = [
                {"type": "text", "text": "Analise estas imagens extraídas de um documento financeiro. Identifique se alguma delas é o LOGOTIPO de um banco ou instituição financeira. Se encontrar, retorne APENAS o nome do banco (ex: 'Sicredi', 'Banco do Brasil', 'Itaú'). Se não encontrar nenhum logo de banco claro, retorne 'null'."}
            ]
            
            for img_b64 in images_base64:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })
                
            message = HumanMessage(content=content_parts)
            
            response = self.llm.invoke([message])
            text = response.content.strip().replace("'", "").replace('"', "").strip()
            
            if text.lower() == "null" or len(text) > 50: # 50 chars é muito para um nome de banco
                return None
                
            logger.info(f"Banco identificado por VISÃO: {text}")
            return text
            
        except Exception as e:
            logger.error(f"Erro na identificação visual do banco: {e}")
            return None
