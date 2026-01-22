"""
Serviço de integração com LLM via LangChain.

Utiliza OpenAI para extrair informações estruturadas de documentos.
"""

import json
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from app.config import get_settings
from app.schemas.llm_response import LLMExtractionResult

logger = logging.getLogger(__name__)

# Prompt do sistema para extração de informações
SYSTEM_PROMPT = """Você é um assistente especializado em análise de documentos financeiros e bancários.
Sua tarefa é extrair informações estruturadas de textos de extratos bancários e documentos contábeis.

REGRAS IMPORTANTES:
1. Extraia APENAS informações que estão explicitamente no texto
2. Se uma informação não estiver clara, use null
3. O campo "confianca" deve refletir sua certeza geral sobre a extração (0.0 a 1.0)
4. Para CNPJ, mantenha o formato encontrado ou extraia apenas números
5. NÃO extraia datas - o sistema usará automaticamente o mês anterior

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

CÓDIGOS CURTOS (use quando apropriado):
- "CC" -> Conta Corrente (alternativa curta)
- "POUPANÇA" -> Poupança (alternativa curta)
- "CARTÃO" -> Cartão de Crédito (alternativa curta)

OUTROS TIPOS:
- "EXTRATO PIX" -> Para extratos de transferências PIX
- "EXTRATO EMPRÉSTIMO" -> Para empréstimos, financiamentos
- "EXTRATO CONSÓRCIO" -> Para consórcios
- "OUTROS" -> Se não se encaixar em nenhuma categoria acima

IDENTIFICAÇÃO DE BANCOS:
- "COOP DE CRED POUP INV SOMA PR/SC/SP" -> Banco: SICREDI
- "SICOOB" ou "COOPERATIVA" -> Banco: SICOOB (NÃO confunda com CRESOL)
- "CRESOL" -> Banco: CRESOL (cooperativa de crédito CRESOL, diferente de SICOOB)
- "CAIXA ECONOMICA" -> Banco: CAIXA
- Sempre retorne o nome SIMPLIFICADO do banco em UPPERCASE (ex: "SICREDI", "SICOOB", "CRESOL", "BRADESCO", "ITAU", "CAIXA", "BANCO DO BRASIL", "SANTANDER")
- Se não conseguir identificar com certeza, retorne null

FORMATO DE RESPOSTA:
Retorne APENAS um JSON válido, sem explicações adicionais:

{
    "cliente_sugerido": "string ou null - nome da empresa/pessoa identificada",
    "cnpj": "string ou null - CNPJ encontrado",
    "banco": "string ou null - nome simplificado do banco em UPPERCASE",
    "agencia": "string ou null - número da agência",
    "conta": "string ou null - número da conta",
    "tipo_documento": "string - OBRIGATÓRIO: Um dos tipos acima",
    "confianca": "number - nível de confiança de 0.0 a 1.0"
}"""


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
        
        # Monta as mensagens para a LLM
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"TEXTO DO DOCUMENTO:\n\n{text}"),
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
            result = self.extract_info(text)

            # Se o banco não foi identificado e temos os dados do PDF, tenta visão
            if (not result.banco or result.banco == "null") and pdf_data:
                logger.info("Banco não identificado no texto, tentando visão...")
                try:
                    from app.services.vision_service import VisionService
                    vision_service = VisionService()
                    banco_visual = vision_service.identify_from_first_page(pdf_data)

                    if banco_visual:
                        logger.info(f"Banco identificado por visão: {banco_visual}")
                        result.banco = banco_visual
                        # Aumenta a confiança se a visão conseguiu identificar
                        if result.confianca < 0.9:
                            result.confianca = 0.9
                except Exception as e:
                    logger.warning(f"Erro ao tentar identificar banco por visão: {e}")

            return result

        except Exception as e:
            logger.warning(f"Usando fallback devido a erro: {e}")

            return LLMExtractionResult(
                cliente_sugerido=None,
                cnpj=None,
                banco=None,
                agencia=None,
                conta=None,
                tipo_documento="OUTROS",
                confianca=0.0,
            )

