"""
Serviço de matching de clientes.

Implementa a lógica de identificação de clientes com prioridade:
1. CNPJ exato
2. Agência + Conta + Banco
3. Nome com similaridade (rapidfuzz)
"""

import logging

from rapidfuzz import fuzz

from app.config import get_settings
from app.schemas.client import ClientInfo, MatchResult, MatchMethod
from app.schemas.llm_response import LLMExtractionResult
from app.services.client_service import ClientService
from app.utils.text import extract_numbers, normalize_text

logger = logging.getLogger(__name__)


class MatchingService:
    """Serviço de matching de clientes."""
    
    def __init__(self, client_service: ClientService | None = None):
        """
        Inicializa o serviço.
        
        Args:
            client_service: Serviço de clientes (opcional, cria um novo se não fornecido)
        """
        self.client_service = client_service or ClientService()
        self.settings = get_settings()
    
    def match(self, extraction: LLMExtractionResult) -> MatchResult:
        """
        Tenta identificar o cliente com base nas informações extraídas.
        
        Segue a ordem de prioridade:
        1. Match por CNPJ exato
        2. Match por Agência + Conta + Banco
        3. Match por similaridade de nome
        
        Args:
            extraction: Resultado da extração da LLM
            
        Returns:
            Resultado do matching com cliente (se encontrado) e metadados
        """
        clients = self.client_service.load_clients()
        
        # 1. Tentar match por CNPJ
        if extraction.cnpj:
            result = self._match_by_cnpj(extraction.cnpj, clients)
            if result.identificado:
                logger.info(f"Match por CNPJ: {result.cliente.nome}")
                return result
        
        # 2. Tentar match por Agência + Conta + Banco
        if extraction.agencia and extraction.conta:
            result = self._match_by_conta(
                extraction.banco,
                extraction.agencia,
                extraction.conta,
                clients
            )
            if result.identificado:
                logger.info(f"Match por Conta/Agência: {result.cliente.nome}")
                return result
        
        # 3. Tentar match por nome com similaridade
        if extraction.cliente_sugerido:
            result = self._match_by_name(extraction.cliente_sugerido, clients)
            if result.identificado:
                logger.info(
                    f"Match por nome ({result.score:.1f}%): {result.cliente.nome}"
                )
                return result
        
        # Nenhum match encontrado
        motivo = self._build_fallback_reason(extraction)
        logger.warning(f"Cliente não identificado: {motivo}")
        
        return MatchResult(
            cliente=None,
            metodo=MatchMethod.NAO_IDENTIFICADO,
            score=0.0,
            motivo_fallback=motivo,
        )
    
    def _match_by_cnpj(
        self, 
        cnpj: str, 
        clients: list[ClientInfo]
    ) -> MatchResult:
        """
        Tenta encontrar cliente pelo CNPJ.
        
        Match exato considerando apenas os números.
        """
        cnpj_numbers = extract_numbers(cnpj)
        
        if len(cnpj_numbers) != 14:
            return MatchResult(
                motivo_fallback=f"CNPJ inválido: {cnpj}"
            )
        
        for client in clients:
            if not client.cnpj:
                continue
            
            client_cnpj = extract_numbers(client.cnpj)
            if client_cnpj == cnpj_numbers:
                return MatchResult(
                    cliente=client,
                    metodo=MatchMethod.CNPJ,
                    score=100.0,
                )
        
        return MatchResult(
            motivo_fallback=f"CNPJ não encontrado na base: {cnpj}"
        )
    
    def _match_by_conta(
        self,
        banco: str | None,
        agencia: str,
        conta: str,
        clients: list[ClientInfo]
    ) -> MatchResult:
        """
        Tenta encontrar cliente pela combinação Banco + Agência + Conta.
        
        Compara apenas os números da agência e conta.
        Se o banco for fornecido, usa como critério adicional.
        """
        agencia_numbers = extract_numbers(agencia)
        conta_numbers = extract_numbers(conta)
        banco_normalized = normalize_text(banco) if banco else None
        
        candidates: list[tuple[ClientInfo, float]] = []
        
        for client in clients:
            if not client.agencia or not client.conta:
                continue
            
            client_agencia = extract_numbers(client.agencia)
            client_conta = extract_numbers(client.conta)
            
            # Verifica se agência e conta batem
            if client_agencia != agencia_numbers or client_conta != conta_numbers:
                continue
            
            # Se chegou aqui, agência e conta batem
            score = 90.0
            
            # Bonus se o banco também bater
            if banco_normalized and client.banco:
                client_banco = normalize_text(client.banco)
                if banco_normalized in client_banco or client_banco in banco_normalized:
                    score = 95.0
            
            candidates.append((client, score))
        
        # Retorna o candidato com maior score
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_client, best_score = candidates[0]
            
            return MatchResult(
                cliente=best_client,
                metodo=MatchMethod.CONTA_AGENCIA,
                score=best_score,
            )
        
        return MatchResult(
            motivo_fallback=f"Conta/Agência não encontrada: Ag {agencia} / Cc {conta}"
        )
    

    def _clean_company_name(self, name: str) -> str:
        """
        Limpa o nome da empresa removendo artefatos comuns de extratos.
        Ex: 'ASSOCIADO...: 12345 - EMPRESA X' -> 'EMPRESA X'
        """
        import re
        
        # Remove prefixos comuns como "ASSOCIADO:", "CLIENTE:", etc, seguido de números/traços
        # Padrão busca algo como "Palavra...: 123-4 -" no início
        cleaned = re.sub(r'^[\w\.]+\s*:\s*[\d\.\-\/]+\s*-\s*', '', name)
        
        # Remove apenas números e traços do início se sobraram
        cleaned = re.sub(r'^[\d\.\-\/]+\s*-\s*', '', cleaned)
        
        return cleaned.strip()

    def _apply_abbreviations(self, text: str) -> str:
        """Aplica abreviações comuns para melhorar o matching."""
        replacements = {
            "COOPERATIVA": "COOP",
            "TRANSPORTADORES": "TRANSP",
            "TRANSPORTES": "TRANSP",
            "TRANSPORTE": "TRANSP",
            "COMERCIO": "COM",
            "LIMITADA": "LTDA",
            "SERVICOS": "SERV",
            "INDUSTRIA": "IND",
            "SOCIEDADE": "SOC",
            "ANONIMA": "SA",
        }
        
        text_upper = text.upper()
        for original, abbr in replacements.items():
            text_upper = text_upper.replace(original, abbr)
            
        return text_upper
    
    def _match_by_name(
        self,
        nome_sugerido: str,
        clients: list[ClientInfo]
    ) -> MatchResult:
        """
        Tenta encontrar cliente por similaridade de nome.
        
        Utiliza rapidfuzz com threshold configurável.
        """
        # Limpa o nome sugerido antes de normalizar
        nome_limpo = self._clean_company_name(nome_sugerido)
        nome_normalized = normalize_text(nome_limpo)
        nome_abbr = normalize_text(self._apply_abbreviations(nome_limpo))
        
        threshold = self.settings.similarity_threshold
        
        best_match: tuple[ClientInfo, float] | None = None
        
        for client in clients:
            client_nome = normalize_text(client.nome)
            
            # Calcula scores para nome normal e nome abreviado
            def get_max_score(target_name: str, candidate_name: str) -> float:
                return max([
                    fuzz.ratio(target_name, candidate_name),
                    fuzz.partial_ratio(target_name, candidate_name),
                    fuzz.token_sort_ratio(target_name, candidate_name),
                    fuzz.token_set_ratio(target_name, candidate_name),
                    fuzz.WRatio(target_name, candidate_name),
                ])
            
            score_normal = get_max_score(nome_normalized, client_nome)
            score_abbr = get_max_score(nome_abbr, client_nome)
            
            score = max(score_normal, score_abbr)
            
            if score >= threshold:
                if best_match is None or score > best_match[1]:
                    best_match = (client, score)
        
        if best_match:
            return MatchResult(
                cliente=best_match[0],
                metodo=MatchMethod.NOME_SIMILARIDADE,
                score=best_match[1],
            )
        
        return MatchResult(
            motivo_fallback=(
                f"Nome '{nome_sugerido}' não encontrou match "
                f"acima do threshold ({threshold}%)"
            )
        )
    
    def _build_fallback_reason(self, extraction: LLMExtractionResult) -> str:
        """Constrói uma mensagem explicando por que não foi possível identificar."""
        reasons = []
        
        if not extraction.cnpj:
            reasons.append("CNPJ não encontrado no documento")
        else:
            reasons.append(f"CNPJ {extraction.cnpj} não cadastrado")
        
        if not extraction.agencia or not extraction.conta:
            reasons.append("Agência/Conta não identificadas")
        else:
            reasons.append(
                f"Ag {extraction.agencia} / Cc {extraction.conta} não cadastradas"
            )
        
        if not extraction.cliente_sugerido:
            reasons.append("Nome do cliente não identificado")
        else:
            reasons.append(
                f"Nome '{extraction.cliente_sugerido}' sem match suficiente"
            )
        
        return "; ".join(reasons)
