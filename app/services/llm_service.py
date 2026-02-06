"""
Serviço de integração com LLM via LangChain.

Utiliza OpenAI para extrair informações estruturadas de documentos.
"""

import json
import logging
import re
import unicodedata
import time
import hashlib
from functools import lru_cache
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from app.config import get_settings
from app.services.client_service import ClientService
from app.utils.text import extract_numbers
from app.schemas.llm_response import LLMExtractionResult

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logging.warning("tiktoken não disponível - usando contagem aproximada de caracteres")

logger = logging.getLogger(__name__)

# Prompt com Chain-of-Thought para casos complexos
SYSTEM_PROMPT_COT = """Você é especialista em extração de dados de documentos financeiros.

<task>
Extrair: cliente_sugerido, cnpj, banco, agencia, conta, contrato, tipo_documento, confianca (0.0-1.0)
</task>

<critical_rules>
1. Extraia APENAS informações explícitas no texto
2. Use null se ausente ou incerto
3. NÃO extraia datas (sistema controla)
4. Foque no CABEÇALHO (primeiras 30 linhas)
5. ⚠️ MATRÍCULA ≠ CONTA! Se só há MATRÍCULA → conta=null
</critical_rules>

<document_types>
Use EXATAMENTE um tipo:
• EXTRATO DE CONTA CORRENTE | EXTRATO DA CONTA CAPITAL | EXTRATO CONTA POUPANÇA
• EXTRATO APLICAÇÃO | EXTRATO CONSOLIDADO RENDA FIXA
• EXTRATO DE FATURA DE CARTÃO DE CRÉDITO | REL RECEBIMENTO
• CONTA GRÁFICA DETALHADA | CONTA GRÁFICA SIMPLIFICADA
• PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS
• PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO
• EXTRATO PIX | EXTRATO EMPRÉSTIMO | EXTRATO CONSÓRCIO | EXTRATO DE RDC | RELATÓRIO DE BOLETOS | OUTROS
</document_types>

<type_priority>
Em caso de sinais conflitantes, use esta prioridade:
1) EXTRATO DA CONTA CAPITAL
2) EXTRATO DE FATURA DE CARTAO DE CREDITO
3) EXTRATO CONTA POUPANCA
4) EXTRATO APLICACAO / EXTRATO CONSOLIDADO RENDA FIXA
5) EXTRATO DE CONTA CORRENTE
6) RELATORIOS ESPECIFICOS (PAR, REL RECEBIMENTO, CONTA GRAFICA)
7) OUTROS
</type_priority>

<type_rules>
Regras negativas (anti-match):
- Se houver FATURA, VENCIMENTO, LIMITE ou CARTAO -> NAO e conta corrente.
- Se houver POUPANCA -> NAO e conta corrente.
- Se houver RENDA FIXA, CDB, FUNDO, APLICACAO, RENDE FACIL -> NAO e conta corrente.
- Se houver CONTA CAPITAL, SUBSCRICAO, CAPITAL SOCIAL, MATRICULA -> NAO e conta corrente/poupanca.
- Se houver "PAR - RELATORIO..." -> classifique como PAR e ignore outros sinais.

Regras positivas de investimento:
- Se houver RENDE FACIL -> tipo_documento = "EXTRATO APLICACAO"
- Se houver RENDA FIXA -> tipo_documento = "EXTRATO CONSOLIDADO RENDA FIXA"
</type_rules>

<bank_identification>
⚠️ CRÍTICO: SICOOB ≠ CRESOL (bancos DIFERENTES!)

SICOOB: "SICOOB" OU "SISBR" OU "SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL"
CRESOL: "CRESOL" (sem SICOOB)
SICREDI: "COOP DE CRED POUP INV SOMA" OU "SICREDI"
BANCO DO BRASIL: "BANCO DO BRASIL" OU "BB S.A" OU "BB"
Outros: BRADESCO | ITAU | SANTANDER | CAIXA

Sempre retorne UPPERCASE. Se incerto → null
</bank_identification>

<extraction_rules>
agencia: Após "Agência|Cooperativa|Ag." → Ex: "3037", "5684", "3037-6"

conta: Após "Conta|Conta Corrente|Cc" → Ex: "75.662-8", "001074-0" (incluir dígito verificador)
  ⚠️ Se só há MATRÍCULA (sem "Conta:") → conta=null

cliente: Após "Nome:|Razão Social:" → Remova "ASSOCIADO:|CLIENTE:|números"
  PAR SICOOB: Cliente em "Cedente" (ignore "Sacado")

cnpj: Formato XX.XXX.XXX/XXXX-XX

contrato: Para empréstimos, após "Número do Contrato|Contrato:"

<special_cases>
• Conta Capital: Se tipo="EXTRATO DA CONTA CAPITAL" → conta=null (sempre)
• Banco do Brasil: "Conta 20000-X NOME" → conta="20000-X", cliente="NOME"
</special_cases>
</extraction_rules>

<reasoning_required>
Antes de retornar o JSON, raciocine em <thinking>:
1. Qual banco? (cite evidências textuais)
2. Onde está agência/conta? (cite linhas/trechos)
3. Há ambiguidade MATRÍCULA vs CONTA?
4. Qual tipo de documento e por quê?
5. Qual confiança final? (justifique)
</reasoning_required>

<output_format>
<thinking>
[Seu raciocínio passo a passo aqui]
</thinking>

<result>
{
  "cliente_sugerido": "string|null",
  "cnpj": "string|null",
  "banco": "string|null (UPPERCASE)",
  "agencia": "string|null",
  "conta": "string|null",
  "contrato": "string|null",
  "tipo_documento": "string (OBRIGATÓRIO)",
  "confianca": 0.0-1.0
}
</result>
</output_format>"""

# Prompt otimizado para extração de informações (V2 - Redução de 60% no tamanho)
SYSTEM_PROMPT = """Você é especialista em extração de dados de documentos financeiros.

<task>
Extrair: cliente_sugerido, cnpj, banco, agencia, conta, contrato, tipo_documento, confianca (0.0-1.0)
</task>

<critical_rules>
1. Extraia APENAS informações explícitas no texto
2. Use null se ausente ou incerto
3. NÃO extraia datas (sistema controla)
4. Foque no CABEÇALHO (primeiras 30 linhas)
5. ⚠️ MATRÍCULA ≠ CONTA! Se só há MATRÍCULA → conta=null
</critical_rules>

<document_types>
Use EXATAMENTE um tipo:
• EXTRATO DE CONTA CORRENTE | EXTRATO DA CONTA CAPITAL | EXTRATO CONTA POUPANÇA
• EXTRATO APLICAÇÃO | EXTRATO CONSOLIDADO RENDA FIXA
• EXTRATO DE FATURA DE CARTÃO DE CRÉDITO | REL RECEBIMENTO
• CONTA GRÁFICA DETALHADA | CONTA GRÁFICA SIMPLIFICADA
• PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS
• PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO
• EXTRATO PIX | EXTRATO EMPRÉSTIMO | EXTRATO CONSÓRCIO | EXTRATO DE RDC | RELATÓRIO DE BOLETOS | OUTROS
</document_types>

<type_priority>
Em caso de sinais conflitantes, use esta prioridade:
1) EXTRATO DA CONTA CAPITAL
2) EXTRATO DE FATURA DE CARTAO DE CREDITO
3) EXTRATO CONTA POUPANCA
4) EXTRATO APLICACAO / EXTRATO CONSOLIDADO RENDA FIXA
5) EXTRATO DE CONTA CORRENTE
6) RELATORIOS ESPECIFICOS (PAR, REL RECEBIMENTO, CONTA GRAFICA)
7) OUTROS
</type_priority>

<type_rules>
Regras negativas (anti-match):
- Se houver FATURA, VENCIMENTO, LIMITE ou CARTAO -> NAO e conta corrente.
- Se houver POUPANCA -> NAO e conta corrente.
- Se houver RENDA FIXA, CDB, FUNDO, APLICACAO, RENDE FACIL -> NAO e conta corrente.
- Se houver CONTA CAPITAL, SUBSCRICAO, CAPITAL SOCIAL, MATRICULA -> NAO e conta corrente/poupanca.
- Se houver "PAR - RELATORIO..." -> classifique como PAR e ignore outros sinais.

Regras positivas de investimento:
- Se houver RENDE FACIL -> tipo_documento = "EXTRATO APLICACAO"
- Se houver RENDA FIXA -> tipo_documento = "EXTRATO CONSOLIDADO RENDA FIXA"
</type_rules>

<bank_identification>
⚠️ CRÍTICO: SICOOB ≠ CRESOL (bancos DIFERENTES!)

SICOOB: "SICOOB" OU "SISBR" OU "SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL"
CRESOL: "CRESOL" (sem SICOOB)
SICREDI: "COOP DE CRED POUP INV SOMA" OU "SICREDI"
BANCO DO BRASIL: "BANCO DO BRASIL" OU "BB S.A" OU "BB"
Outros: BRADESCO | ITAU | SANTANDER | CAIXA

Sempre retorne UPPERCASE. Se incerto → null
</bank_identification>

<extraction_rules>
agencia: Após "Agência|Cooperativa|Ag." → Ex: "3037", "5684", "3037-6"

conta: Após "Conta|Conta Corrente|Cc" → Ex: "75.662-8", "001074-0" (incluir dígito verificador)
  ⚠️ Se só há MATRÍCULA (sem "Conta:") → conta=null

cliente: Após "Nome:|Razão Social:" → Remova "ASSOCIADO:|CLIENTE:|números"
  PAR SICOOB: Cliente em "Cedente" (ignore "Sacado")

cnpj: Formato XX.XXX.XXX/XXXX-XX

contrato: Para empréstimos, após "Número do Contrato|Contrato:"

<special_cases>
• Conta Capital: Se tipo="EXTRATO DA CONTA CAPITAL" → conta=null (sempre)
• Banco do Brasil: "Conta 20000-X NOME" → conta="20000-X", cliente="NOME"
</special_cases>
</extraction_rules>

<output_format>
Retorne APENAS JSON válido:
{
  "cliente_sugerido": "string|null",
  "cnpj": "string|null",
  "banco": "string|null (UPPERCASE)",
  "agencia": "string|null",
  "conta": "string|null",
  "contrato": "string|null",
  "tipo_documento": "string (OBRIGATÓRIO)",
  "confianca": 0.0-1.0
}
</output_format>"""

# Prompt especifico para arquivos OFX
OFX_SYSTEM_PROMPT = """Você é um especialista em extração de dados de arquivos OFX (Open Financial Exchange).
Seu objetivo é extrair com precisão: banco, agência, conta e tipo de documento.

## REGRAS FUNDAMENTAIS
1. Extraia APENAS dados explicitamente presentes no OFX
2. NÃO invente, deduza ou infira informações
3. Se um dado não existir, retorne null
4. Você pode preservar o formato original de agência/conta (incluindo hífens)
   Nota: o sistema irá normalizar agência/conta para apenas dígitos após a extração
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
IMPORTANTE: Para OFX, o tipo_documento DEVE ser APENAS uma destas duas opcoes:
- "EXTRATO DE CONTA CORRENTE"
- "EXTRATO DA CONTA CAPITAL"


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
Retorne APENAS JSON válido.
Use null literal (sem aspas) quando o valor estiver ausente.
O campo "confianca" deve ser número (não string).
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

# Prompt especializado para Conta Capital
CONTA_CAPITAL_PROMPT = """Especialista em extração de EXTRATO DA CONTA CAPITAL.

<critical_warning>
⚠️ EXTRATO DA CONTA CAPITAL NÃO possui campo "Conta"!
Existe apenas MATRÍCULA, mas MATRÍCULA ≠ CONTA
SEMPRE retorne conta=null para este tipo de documento
</critical_warning>

<extraction>
banco: Procure "SICOOB|SISBR|SISTEMA DE COOPERATIVAS" (UPPERCASE)
agencia: Após "Cooperativa:" → Ex: "3037"
conta: SEMPRE null (não há conta em extrato de capital!)
cliente: Após "MATRÍCULA:" → Ex: "MATRÍCULA: 1038907 - EMPRESA X" → "EMPRESA X"
tipo_documento: SEMPRE "EXTRATO DA CONTA CAPITAL"
confianca: 0.9 se identificou banco+agencia
</extraction>

<output>
{
  "cliente_sugerido": "string|null",
  "cnpj": "string|null",
  "banco": "string|null (UPPERCASE)",
  "agencia": "string|null",
  "conta": null,
  "contrato": null,
  "tipo_documento": "EXTRATO DA CONTA CAPITAL",
  "confianca": 0.0-1.0
}
</output>"""

# Prompt especializado para Empréstimos
EMPRESTIMO_PROMPT = """Especialista em extração de EXTRATO DE EMPRÉSTIMO.

<extraction>
banco: Procure nome do banco (UPPERCASE)
agencia: Após "Agência|Cooperativa"
conta: Após "Conta" (se houver)
contrato: ⚠️ OBRIGATÓRIO! Após "Número do Contrato:|Contrato:" → Ex: "123456789"
cliente: Após "Nome:|Cliente:"
tipo_documento: "EXTRATO EMPRÉSTIMO"
confianca: 0.9 se identificou contrato
</extraction>

<output>
{
  "cliente_sugerido": "string|null",
  "cnpj": "string|null",
  "banco": "string|null (UPPERCASE)",
  "agencia": "string|null",
  "conta": "string|null",
  "contrato": "string (OBRIGATÓRIO)",
  "tipo_documento": "EXTRATO EMPRÉSTIMO",
  "confianca": 0.0-1.0
}
</output>"""

# Exemplos para few-shot dinâmico
FEW_SHOT_EXAMPLES = {
    "sicoob_consolidado": {
        "input": """SICOOB
SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL
EXTRATO CONSOLIDADO RENDA FIXA
Cooperativa: 3037
Conta: 75.662-8
Nome: COMERCIAL SUL BRASIL LTDA
CNPJ: 82.697.137/0001-01""",
        "output": {
            "cliente_sugerido": "COMERCIAL SUL BRASIL LTDA",
            "cnpj": "82.697.137/0001-01",
            "banco": "SICOOB",
            "agencia": "3037",
            "conta": "75.662-8",
            "contrato": None,
            "tipo_documento": "EXTRATO CONSOLIDADO RENDA FIXA",
            "confianca": 0.95
        }
    },
    "banco_brasil": {
        "input": """BANCO DO BRASIL S.A.
Cliente - Conta atual
Agência: 5684
Conta corrente 20000-6 SUPERMERCADO MARTELLI LTDA""",
        "output": {
            "cliente_sugerido": "SUPERMERCADO MARTELLI LTDA",
            "cnpj": None,
            "banco": "BANCO DO BRASIL",
            "agencia": "5684",
            "conta": "20000-6",
            "contrato": None,
            "tipo_documento": "EXTRATO DE CONTA CORRENTE",
            "confianca": 0.9
        }
    },
    "conta_capital": {
        "input": """SICOOB
EXTRATO DA CONTA CAPITAL
Cooperativa: 3037
MATRÍCULA: 1038907 - EMPRESA EXEMPLO LTDA
CNPJ: 12.345.678/0001-99""",
        "output": {
            "cliente_sugerido": "EMPRESA EXEMPLO LTDA",
            "cnpj": "12.345.678/0001-99",
            "banco": "SICOOB",
            "agencia": "3037",
            "conta": None,
            "contrato": None,
            "tipo_documento": "EXTRATO DA CONTA CAPITAL",
            "confianca": 0.9
        }
    }
}

class LLMService:
    """Serviço de extração de informações usando LLM com otimizações V2."""

    def __init__(self):
        """Inicializa o serviço com as configurações."""
        settings = get_settings()

        # Modelo rápido para casos simples (60% mais barato, 2x mais rápido)
        self.llm_fast = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=settings.openai_api_key,
            temperature=0,
            max_tokens=300,  # JSON é pequeno
        )

        # Modelo avançado para casos complexos
        self.llm_advanced = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            temperature=0,
            max_tokens=500,  # Mais espaço para reasoning
        )

        # Modelo padrão (mantém compatibilidade)
        self.llm = self.llm_fast

        self.parser = JsonOutputParser()

        # Inicializa tokenizer se disponível
        if TIKTOKEN_AVAILABLE:
            try:
                self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                self.tokenizer = None
                logger.warning("Não foi possível inicializar tokenizer tiktoken")
        else:
            self.tokenizer = None

        # Métricas acumuladas da sessão
        self.metrics = {
            "fast_count": 0,
            "advanced_count": 0,
            "total_requests": 0,
            "total_cost": 0.0,
            "total_latency": 0.0,
            "avg_latency": 0.0,
            "validation_warnings": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }

    def _count_tokens(self, text: str) -> int:
        """Conta tokens no texto usando tiktoken ou aproximação."""
        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text))
            except Exception:
                pass
        # Fallback: aproximação (1 token ≈ 4 caracteres)
        return len(text) // 4

    def _truncate_text_by_tokens(self, text: str, max_tokens: int = 12000) -> str:
        """Trunca texto baseado em contagem de tokens."""
        if self.tokenizer:
            try:
                tokens = self.tokenizer.encode(text)
                if len(tokens) > max_tokens:
                    truncated_tokens = tokens[:max_tokens]
                    text = self.tokenizer.decode(truncated_tokens)
                    text += "\n\n[TEXTO TRUNCADO...]"
                return text
            except Exception:
                pass

        # Fallback: trunca por caracteres
        max_chars = max_tokens * 4  # Aproximação
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[TEXTO TRUNCADO...]"
        return text

    def _is_complex_case(self, text: str) -> bool:
        """Detecta se é um caso complexo que precisa do modelo avançado."""
        if not text:
            return True

        normalized = self._normalize_text_for_hint(text[:2000])

        # Casos complexos:
        # 1. Múltiplos bancos mencionados (pode ser confuso)
        bank_keywords = ["SICOOB", "CRESOL", "SICREDI", "BANCO DO BRASIL", "BRADESCO", "ITAU", "SANTANDER"]
        bank_count = sum(1 for keyword in bank_keywords if keyword in normalized)
        if bank_count > 1:
            return True

        # 2. Documento sem cabeçalho claro
        if self._needs_header_ocr(text):
            return True

        # 3. Texto muito curto ou muito longo
        if len(text) < 100 or len(text) > 20000:
            return True

        # 4. Documentos complexos específicos
        complex_types = ["PAR", "CONTA GRAFICA", "CONSOLIDADO"]
        if any(ct in normalized for ct in complex_types):
            return True

        return False

    def _detect_document_type_hint(self, text: str) -> str | None:
        """Detecta tipo de documento para selecionar prompt especializado."""
        normalized = self._normalize_text_for_hint(text[:1000])

        if "CONTA CAPITAL" in normalized or "CAPITAL SOCIAL" in normalized:
            return "conta_capital"
        if "EMPRESTIMO" in normalized or "NUMERO DO CONTRATO" in normalized:
            return "emprestimo"
        if "SICOOB" in normalized and "CONSOLIDADO" in normalized:
            return "sicoob_consolidado"
        if "BANCO DO BRASIL" in normalized:
            return "banco_brasil"

        return None

    def _build_few_shot_examples(self, doc_type_hint: str | None) -> str:
        """Constrói exemplos few-shot relevantes baseado no tipo detectado."""
        if not doc_type_hint or doc_type_hint not in FEW_SHOT_EXAMPLES:
            return ""

        example = FEW_SHOT_EXAMPLES[doc_type_hint]
        example_text = f"""
<example>
<input>
{example['input']}
</input>

<output>
{json.dumps(example['output'], ensure_ascii=False, indent=2)}
</output>
</example>
"""
        return example_text

    def _select_system_prompt_v2(self, text: str, use_cot: bool = False) -> str:
        """Seleciona prompt otimizado baseado no tipo e complexidade."""
        # OFX tem prompt próprio
        if self._is_ofx_text(text):
            return OFX_SYSTEM_PROMPT

        # Detecta tipo específico
        doc_type = self._detect_document_type_hint(text)

        # Prompts especializados
        if doc_type == "conta_capital":
            return CONTA_CAPITAL_PROMPT
        if doc_type == "emprestimo":
            return EMPRESTIMO_PROMPT

        # Chain-of-Thought para casos complexos
        if use_cot:
            return SYSTEM_PROMPT_COT

        # Prompt padrão otimizado
        return SYSTEM_PROMPT

    def _get_text_hash(self, text: str) -> str:
        """Gera hash MD5 do texto para cache."""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def _estimate_cost(self, input_tokens: int, output_tokens: int, is_complex: bool) -> float:
        """Estima custo da requisição baseado no modelo usado."""
        if is_complex:
            # gpt-4o: $2.50/1M input, $10.00/1M output
            input_cost = (input_tokens / 1_000_000) * 2.50
            output_cost = (output_tokens / 1_000_000) * 10.00
        else:
            # gpt-4o-mini: $0.150/1M input, $0.600/1M output
            input_cost = (input_tokens / 1_000_000) * 0.150
            output_cost = (output_tokens / 1_000_000) * 0.600

        return input_cost + output_cost

    def _validate_extraction(self, result: LLMExtractionResult, text: str) -> list[str]:
        """Valida resultado e retorna warnings de inconsistências."""
        warnings = []

        # 1. CNPJ inválido (formato básico)
        if result.cnpj:
            cnpj_digits = result.cnpj.replace(".", "").replace("/", "").replace("-", "")
            if len(cnpj_digits) != 14 or not cnpj_digits.isdigit():
                warnings.append(f"CNPJ com formato suspeito: {result.cnpj}")

        # 2. Conta Capital não deveria ter conta
        if result.tipo_documento == "EXTRATO DA CONTA CAPITAL" and result.conta:
            warnings.append(f"ATENÇÃO: Conta Capital não deveria ter número de conta! conta={result.conta}")

        # 3. Empréstimo sem contrato
        if "EMPRESTIMO" in (result.tipo_documento or "").upper() and not result.contrato:
            warnings.append("Empréstimo deveria ter número de contrato")

        # 4. Banco não identificado mas há pistas no texto
        if not result.banco or result.banco == "null":
            normalized = self._normalize_text_for_hint(text[:1000])
            if any(bank in normalized for bank in ["SICOOB", "BANCO DO BRASIL", "SICREDI", "BRADESCO"]):
                warnings.append("Banco não identificado mas há pistas óbvias no texto")

        # 5. Agência ou conta vazia
        if not result.agencia and not result.conta and result.tipo_documento != "EXTRATO DA CONTA CAPITAL":
            warnings.append("Nem agência nem conta foram identificadas")

        # 6. Confiança muito baixa
        if result.confianca < 0.5:
            warnings.append(f"Confiança muito baixa: {result.confianca:.2f}")

        return warnings

    def get_metrics(self) -> dict:
        """Retorna métricas acumuladas da sessão."""
        total = self.metrics["total_requests"]
        return {
            **self.metrics,
            "fast_percentage": (self.metrics["fast_count"] / total * 100) if total > 0 else 0,
            "advanced_percentage": (self.metrics["advanced_count"] / total * 100) if total > 0 else 0,
            "cache_hit_rate": (self.metrics["cache_hits"] / (self.metrics["cache_hits"] + self.metrics["cache_misses"]) * 100) if (self.metrics["cache_hits"] + self.metrics["cache_misses"]) > 0 else 0
        }

    def reset_metrics(self):
        """Reseta métricas acumuladas."""
        self.metrics = {
            "fast_count": 0,
            "advanced_count": 0,
            "total_requests": 0,
            "total_cost": 0.0,
            "total_latency": 0.0,
            "avg_latency": 0.0,
            "validation_warnings": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }

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
        only_numbers = re.sub(r'[^0-9]', '', value)
        # Remove zeros à esquerda (mas mantém "0" se for só zeros)
        return only_numbers

    @lru_cache(maxsize=100)
    def _extract_info_cached(self, text_hash: str, text: str) -> tuple:
        """Método interno cacheado (retorna tuple para ser hashable)."""
        return self._extract_info_internal(text)

    def extract_info(self, text: str) -> LLMExtractionResult:
        """
        Extrai informações estruturadas do texto do documento (V2 otimizado + cache + métricas).

        Utiliza a LLM para analisar o texto e retornar um JSON
        com as informações identificadas.

        Args:
            text: Texto extraído do documento PDF

        Returns:
            Resultado da extração com todas as informações

        Raises:
            Exception: Se houver erro na chamada da LLM ou parsing
        """
        # Verifica cache
        text_hash = self._get_text_hash(text)
        cache_info = self._extract_info_cached.cache_info()
        initial_hits = cache_info.hits

        try:
            # Tenta usar cache
            result_tuple = self._extract_info_cached(text_hash, text)
            result = LLMExtractionResult(**result_tuple)

            # Verifica se foi cache hit
            cache_info_after = self._extract_info_cached.cache_info()
            if cache_info_after.hits > initial_hits:
                self.metrics["cache_hits"] += 1
                logger.info(f"✓ Cache hit para documento (hash={text_hash[:8]}...)")
            else:
                self.metrics["cache_misses"] += 1

            return result

        except Exception as e:
            logger.error(f"Erro na extração com cache: {e}")
            raise

    def _extract_info_internal(self, text: str) -> dict:
        """Método interno de extração (sem cache)."""
        start_time = time.time()

        # Trunca texto baseado em tokens (deixa 4k para prompt+resposta)
        text = self._truncate_text_by_tokens(text, max_tokens=12000)

        # Detecta complexidade e seleciona modelo apropriado
        is_complex = self._is_complex_case(text)
        use_cot = is_complex  # Chain-of-Thought para casos complexos
        llm = self.llm_advanced if is_complex else self.llm_fast

        if is_complex:
            logger.info("Caso complexo detectado - usando modelo avançado com CoT")
        else:
            logger.info("Caso simples - usando modelo rápido")

        # Seleciona prompt apropriado
        system_prompt = self._select_system_prompt_v2(text, use_cot=use_cot)

        # Adiciona exemplos few-shot se relevante
        doc_type_hint = self._detect_document_type_hint(text)
        few_shot = self._build_few_shot_examples(doc_type_hint)

        # Monta as mensagens para a LLM
        messages = [
            SystemMessage(content=system_prompt),
        ]

        if few_shot:
            messages.append(HumanMessage(content=few_shot))

        messages.append(HumanMessage(content=f"<document>\n{text}\n</document>"))

        try:
            # Chama a LLM
            response = llm.invoke(messages)

            # Extrai o conteúdo da resposta
            content = response.content.strip()

            # Se usou CoT, extrai apenas o resultado
            if use_cot and "<result>" in content:
                match = re.search(r"<result>(.*?)</result>", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()

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

            # Normaliza agência e conta (remove pontos, hifens, mantém apenas números)
            if result.agencia:
                result.agencia = self._normalize_account_field(result.agencia)
            if result.conta:
                result.conta = self._normalize_account_field(result.conta)

            # Calcula métricas
            elapsed = time.time() - start_time
            input_tokens = self._count_tokens(system_prompt + (few_shot or "") + text)
            output_tokens = self._count_tokens(content)
            cost = self._estimate_cost(input_tokens, output_tokens, is_complex)

            # Atualiza métricas
            self.metrics["total_requests"] += 1
            if is_complex:
                self.metrics["advanced_count"] += 1
            else:
                self.metrics["fast_count"] += 1
            self.metrics["total_cost"] += cost
            self.metrics["total_latency"] += elapsed
            self.metrics["avg_latency"] = self.metrics["total_latency"] / self.metrics["total_requests"]

            # Validação pós-extração
            validation_warnings = self._validate_extraction(result, text)
            if validation_warnings:
                self.metrics["validation_warnings"] += len(validation_warnings)
                for warning in validation_warnings:
                    logger.warning(f"⚠️ Validação: {warning}")

            logger.info(
                f"Extração LLM V2 concluída: cliente={result.cliente_sugerido}, "
                f"banco={result.banco}, confianca={result.confianca}, "
                f"modelo={'avançado' if is_complex else 'rápido'}, "
                f"latência={elapsed:.2f}s, custo=~${cost:.4f}, tokens={input_tokens}+{output_tokens}"
            )

            # Retorna como dict para ser hashable no cache
            return {
                "cliente_sugerido": result.cliente_sugerido,
                "cnpj": result.cnpj,
                "banco": result.banco,
                "agencia": result.agencia,
                "conta": result.conta,
                "contrato": result.contrato,
                "tipo_documento": result.tipo_documento,
                "confianca": result.confianca
            }

        except json.JSONDecodeError as e:
            logger.error(f"Erro ao fazer parse do JSON da LLM: {e}")
            logger.debug(f"Resposta da LLM: {response.content}")
            raise ValueError(f"Resposta da LLM não é um JSON válido: {e}")

        except Exception as e:
            # Fallback inteligente: se falhou no modelo rápido, tenta avançado
            if not is_complex and "rate limit" not in str(e).lower():
                logger.warning(f"Modelo rápido falhou ({e}). Tentando modelo avançado como fallback...")
                try:
                    # Retry com modelo avançado
                    llm = self.llm_advanced
                    response = llm.invoke(messages)
                    content = response.content.strip()

                    # Processa resposta (mesmo código de antes)
                    if "<result>" in content:
                        match = re.search(r"<result>(.*?)</result>", content, re.DOTALL)
                        if match:
                            content = match.group(1).strip()

                    if content.startswith("```json"):
                        content = content[7:]
                    if content.startswith("```"):
                        content = content[3:]
                    if content.endswith("```"):
                        content = content[:-3]

                    data = json.loads(content.strip())
                    result = LLMExtractionResult(**data)

                    if result.agencia:
                        result.agencia = self._normalize_account_field(result.agencia)
                    if result.conta:
                        result.conta = self._normalize_account_field(result.conta)

                    elapsed = time.time() - start_time
                    input_tokens = self._count_tokens(system_prompt + (few_shot or "") + text)
                    output_tokens = self._count_tokens(content)
                    cost = self._estimate_cost(input_tokens, output_tokens, True)

                    self.metrics["total_requests"] += 1
                    self.metrics["advanced_count"] += 1
                    self.metrics["total_cost"] += cost
                    self.metrics["total_latency"] += elapsed
                    self.metrics["avg_latency"] = self.metrics["total_latency"] / self.metrics["total_requests"]

                    logger.info(f"✓ Fallback para modelo avançado bem-sucedido (latência={elapsed:.2f}s, custo=~${cost:.4f})")

                    return {
                        "cliente_sugerido": result.cliente_sugerido,
                        "cnpj": result.cnpj,
                        "banco": result.banco,
                        "agencia": result.agencia,
                        "conta": result.conta,
                        "contrato": result.contrato,
                        "tipo_documento": result.tipo_documento,
                        "confianca": result.confianca
                    }

                except Exception as fallback_error:
                    logger.error(f"Fallback também falhou: {fallback_error}")
                    raise

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
            analysis_text = text or ""
            if pdf_data and self._needs_header_ocr(analysis_text):
                header_text = self._try_extract_header_text(pdf_data)
                if header_text:
                    analysis_text = f"{header_text}\n\n{analysis_text}"

            result = self.extract_info(analysis_text)

            # OFX: agencia/conta devem vir das tags BRANCHID/ACCTID
            if self._is_ofx_text(analysis_text):
                self._apply_ofx_branch_acct(result, analysis_text)

            # Heurística textual: confirma banco por pistas fortes no texto
            # Ajuste para OFX do SICREDI: agencia = primeiros digitos, conta = resto sem zeros a esquerda
            if self._is_ofx_text(analysis_text) and result.conta:
                if self._is_sicredi_text(analysis_text) or (result.banco and "SICREDI" in result.banco.upper()):
                    self._adjust_sicredi_ofx_account(result)
            # Regra fixa para OFX: apenas CONTA CORRENTE ou CONTA CAPITAL
            if self._is_ofx_text(analysis_text):
                if len(analysis_text) > 10000:
                    result.tipo_documento = "EXTRATO DE CONTA CORRENTE"
                else:
                    result.tipo_documento = "EXTRATO DA CONTA CAPITAL"
                if result.confianca < 0.85:
                    result.confianca = 0.85


            banco_hint = self._infer_bank_from_text_hints(analysis_text)
            if banco_hint:
                if not result.banco or result.banco == "null" or result.banco != banco_hint:
                    logger.info(f"Banco ajustado por pista textual: {banco_hint}")
                result.banco = banco_hint
                if result.confianca < 0.85:
                    result.confianca = 0.85

            # Fallback: tenta extrair conta do texto (ex.: planilhas XLS)
            if not result.conta:
                conta_fallback = self._extract_conta_from_text_fallback(analysis_text)
                if conta_fallback:
                    result.conta = conta_fallback
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
            if self._has_rende_facil_hint(analysis_text):
                result.tipo_documento = "EXTRATO APLICACAO"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heurística textual: Extrato de RDC
            if self._has_rdc_hint(analysis_text):
                result.tipo_documento = "EXTRATO DE RDC"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heurística textual: Relatório de Boletos
            if self._has_relatorio_boletos_hint(analysis_text):
                result.tipo_documento = "RELATÓRIO DE BOLETOS"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heurística textual: Extrato Consolidado Renda Fixa
            if self._has_renda_fixa_hint(analysis_text):
                result.tipo_documento = "EXTRATO CONSOLIDADO RENDA FIXA"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heurística textual: Extrato de Conta Corrente
            if self._has_conta_corrente_hint(analysis_text):
                result.tipo_documento = "EXTRATO DE CONTA CORRENTE"
                if result.confianca < 0.8:
                    result.confianca = 0.8

            # Heuristica textual: relatorio PAR do SICOOB (parcelas em aberto/liquidadas)
            par_tipo = self._detect_par_report_type(analysis_text)
            if par_tipo:
                result.tipo_documento = par_tipo
                if result.confianca < 0.85:
                    result.confianca = 0.85

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
        """Seleciona o prompt do sistema baseado no tipo de conteudo (mantido para compatibilidade)."""
        return self._select_system_prompt_v2(text, use_cot=False)

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

    def _is_sicredi_text(self, text: str) -> bool:
        """Detecta pistas fortes de SICREDI no texto."""
        normalized = self._normalize_text_for_hint(text)
        return "SICREDI" in normalized or "COOP DE CRED POUP INV SOMA" in normalized

    def _adjust_sicredi_ofx_account(self, result: LLMExtractionResult) -> None:
        """Ajusta agencia/conta para OFX do SICREDI com base no ACCTID."""
        conta_numbers = extract_numbers(result.conta or "")
        if len(conta_numbers) < 10:
            return
        agencia = conta_numbers[:3]
        conta_raw = conta_numbers[3:]
        conta = conta_raw.lstrip("0") or "0"
        result.agencia = agencia
        result.conta = conta
        if not result.banco:
            result.banco = "SICREDI"


        return None

    def _extract_ofx_tags(self, text: str) -> tuple[str | None, str | None]:
        """Extrai BRANCHID e ACCTID de OFX (XML ou SGML)."""
        if not text:
            return None, None
        def _find_tag(tag: str) -> str | None:
            # XML: <TAG>value</TAG>
            m = re.search(r"<%s>\s*([^<\n\r]+)" % tag, text, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
            # SGML: <TAG>value (ate fim da linha)
            m = re.search(r"<%s>\s*([^\n\r]+)" % tag, text, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
            return None
        branch = _find_tag('BRANCHID')
        acct = _find_tag('ACCTID')
        return branch, acct

    def _apply_ofx_branch_acct(self, result: LLMExtractionResult, text: str) -> None:
        """Sobrescreve agencia/conta com base nas tags OFX quando disponiveis."""
        branch, acct = self._extract_ofx_tags(text)
        if branch:
            result.agencia = branch
        if acct:
            result.conta = acct



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

    def _has_rdc_hint(self, text: str) -> bool:
        """Detecta indicio de extrato RDC."""
        normalized = self._normalize_text_for_hint(text)
        return "EXTRATO DE RDC" in normalized or "RDC" in normalized

    def _has_relatorio_boletos_hint(self, text: str) -> bool:
        """Detecta indicio de relatorio de boletos."""
        normalized = self._normalize_text_for_hint(text)
        return "RELATORIO DE BOLETOS" in normalized or "RELATORIOS DE BOLETOS" in normalized

    def _has_renda_fixa_hint(self, text: str) -> bool:
        """Detecta indicio de extrato consolidado renda fixa."""
        normalized = self._normalize_text_for_hint(text)
        return "EXTRATO CONSOLIDADO RENDA FIXA" in normalized or "RENDA FIXA" in normalized

    def _has_conta_corrente_hint(self, text: str) -> bool:
        """Detecta indicio de extrato de conta corrente."""
        normalized = self._normalize_text_for_hint(text)
        return "EXTRATO DE CONTA CORRENTE" in normalized

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

    def _extract_conta_from_text_fallback(self, text: str) -> str | None:
        """Extrai conta do texto quando a LLM nao identificou."""
        if not text:
            return None
        normalized_text = unicodedata.normalize("NFKD", text)
        normalized_text = normalized_text.encode("ascii", "ignore").decode("ascii")

        patterns = [
            r"\bCONTA\s+CORRENTE\b\s*[:\-]?\s*([0-9][0-9.\-]{2,20})",
            r"\bCONTA\b\s*[:\-]?\s*([0-9][0-9.\-]{2,20})",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                numbers = extract_numbers(value)
                return numbers or None
        return None


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
