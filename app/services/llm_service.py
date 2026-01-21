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
Você DEVE classificar o documento em EXATAMENTE UMA das categorias abaixo. Não use outros textos.
- "CC" -> Para Extratos de Conta Corrente
- "POUPANCA" -> Para Aplicação Poupança
- "INVESTIMENTO" -> Para Aplicação Investimento, CDB, Fundos
- "CARTAO_CREDITO" -> Para Cartão de Crédito, Maquininhas, Antecipação, Taxas de Cartão
- "CONSORCIO" -> Para Extratos de Consórcio
- "EMPRESTIMO" -> Para Empréstimos, Financiamentos, Mútuos
- "CONTA_CAPITAL" -> Para Extratos de Conta Capital, Capital Social
- "PIX" -> Para Extratos de Transferências PIX, Comprovantes PIX
- "PAGAMENTOS" -> Para Pagamentos Realizados, Comprovantes de Pagamento
- "DEPOSITO" -> Para Extratos de Depósito, Comprovantes de Depósito
- "VENDAS" -> Para Histórico de Vendas, Relatório de Vendas
- "TITULOS_CADASTRADOS" -> Para Relação de Títulos Cadastrados
- "OUTROS" -> Se não se encaixar em nenhuma acima

DICAS PARA IDENTIFICAÇÃO DE BANCOS:
- "COOP DE CRED POUP INV SOMA PR/SC/SP" ou similar -> Banco: SICREDI
- Sempre retorne o nome simplificado do banco (ex: "SICREDI", "BRADESCO", "ITAU", "CAIXA", "BANCO DO BRASIL")

FORMATO DE RESPOSTA:
Retorne APENAS um JSON válido, sem explicações adicionais, com a seguinte estrutura:

{
    "cliente_sugerido": "string ou null - nome da empresa/pessoa identificada. IMPORTANTE: Remova prefixos como 'ASSOCIADO:', 'CLIENTE:', números de conta e códigos que antecedem o nome. Ex: de 'ASSOCIADO: 123 - EMPRESA X' retorne apenas 'EMPRESA X'",
    "cnpj": "string ou null - CNPJ encontrado",
    "banco": "string ou null - nome do banco (ex: Bradesco, Itaú, Banco do Brasil)",
    "agencia": "string ou null - número da agência",
    "conta": "string ou null - número da conta",
    "tipo_documento": "string - OBRIGATÓRIO: Um dos códigos acima (ex: 'CC', 'PIX', 'PAGAMENTOS')",
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
    
    def extract_info_with_fallback(self, text: str) -> LLMExtractionResult:
        """
        Extrai informações com fallback para valores padrão.
        
        Se a extração falhar, retorna um resultado com valores padrão.
        
        Args:
            text: Texto extraído do documento PDF
            
        Returns:
            Resultado da extração (real ou fallback)
        """
        try:
            return self.extract_info(text)
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
