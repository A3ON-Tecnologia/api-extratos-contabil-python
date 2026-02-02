"""
Serviço de integração com LLM via LangChain.

Utiliza OpenAI para extrair informações estruturadas de documentos.
"""

import json
import logging
import re
import unicodedata

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from app.config import get_settings
from app.services.client_service import ClientService
from app.utils.text import extract_numbers
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
- "BANCO DO BRASIL" ou "BB" -> Banco: BANCO DO BRASIL
- Sempre retorne o nome SIMPLIFICADO do banco em UPPERCASE (ex: "SICREDI", "SICOOB", "CRESOL", "BRADESCO", "ITAU", "CAIXA", "BANCO DO BRASIL", "SANTANDER")
- Se não conseguir identificar com certeza, retorne null

EXTRAÇÃO DE NOME/AGÊNCIA/CONTA:
- cliente_sugerido: use o nome da empresa/pessoa do cabeçalho (perto de "Agência/Conta")
- NÃO use nome do banco como cliente_sugerido
- agencia: valor numérico após "Agência" (ou "Agencia")
- conta: valor numérico após "Conta"
- Banco do Brasil: no bloco "Cliente - Conta atual", a linha "Conta corrente" traz "numero - NOME". Use o numero como conta e o texto após o hífen como cliente_sugerido.
- Banco do Brasil (exemplo): "Conta corrente 20000-X SUPERMERCADO MARTELLI LTD" -> conta="20000-X", cliente_sugerido="SUPERMERCADO MARTELLI LTD"
- Banco do Brasil (sem espaco apos a conta): "Conta corrente 20100-6IRMAOS ROSSATO E CIA LTDA" -> conta="20100-6", cliente_sugerido="IRMAOS ROSSATO E CIA LTDA"

FORMATO DE RESPOSTA:
Retorne APENAS um JSON válido, sem explicações adicionais:

{
    "cliente_sugerido": "string ou null - nome da empresa/pessoa identificada. IMPORTANTE: Remova prefixos como 'ASSOCIADO:', 'CLIENTE:', números de conta e códigos que antecedem o nome. Ex: de 'ASSOCIADO: 123 - EMPRESA X' retorne apenas 'EMPRESA X'",
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

            # Heurística textual: confirma banco por pistas fortes no texto
            banco_hint = self._infer_bank_from_text_hints(text)
            if banco_hint:
                if not result.banco or result.banco == "null" or result.banco != banco_hint:
                    logger.info(f"Banco ajustado por pista textual: {banco_hint}")
                result.banco = banco_hint
                if result.confianca < 0.85:
                    result.confianca = 0.85

            # Heurística textual: RENDE FACIL indica extrato de investimento
            if self._has_rende_facil_hint(text):
                result.tipo_documento = "EXTRATO APLICACAO"
                if result.confianca < 0.8:
                    result.confianca = 0.8

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
                tipo_documento="OUTROS",
                confianca=0.0,
            )

    def _infer_bank_from_clients(self, agencia: str, conta: str) -> str | None:
        """Infere o banco usando a planilha de clientes (agência + conta)."""
        def _normalize_number(value: str) -> str:
            numbers = extract_numbers(value)
            # Remove zeros à esquerda para evitar mismatch por formatação
            return numbers.lstrip("0") or "0"

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
            if _normalize_number(client.agencia) != agencia_numbers:
                continue
            if _normalize_number(client.conta) != conta_numbers:
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

        # Banco do Brasil - pistas fortes
        if "OUVIDORIA BB 0800 729 5678" in normalized:
            return "BANCO DO BRASIL"
        if "SAC 0800 729 0722" in normalized:
            return "BANCO DO BRASIL"
        if "BANCO DO BRASIL" in normalized or "BANCO DO BRASIL SA" in normalized:
            return "BANCO DO BRASIL"

        return None

    def _has_rende_facil_hint(self, text: str) -> bool:
        """Detecta indicio de investimento pelo termo RENDE FACIL."""
        normalized = self._normalize_text_for_hint(text)
        return "RENDE FACIL" in normalized


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
