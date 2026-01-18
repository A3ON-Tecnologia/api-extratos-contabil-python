"""
Testes unitários para o serviço de matching.
"""

import pytest

from app.schemas.client import ClientInfo, MatchMethod
from app.schemas.llm_response import LLMExtractionResult
from app.services.matching_service import MatchingService
from app.services.client_service import ClientService


# Mock de clientes para testes
MOCK_CLIENTS = [
    ClientInfo(
        cod="098",
        nome="JP CONTABIL LTDA",
        cnpj="12345678000190",
        banco="BRADESCO",
        agencia="1234",
        conta="123456",
    ),
    ClientInfo(
        cod="089",
        nome="IRMÃOS ARALDI COMERCIO E TRANSPORTE LTDA",
        cnpj="98765432000150",
        banco="ITAU",
        agencia="5678",
        conta="654321",
    ),
    ClientInfo(
        cod="100",
        nome="EMPRESA TESTE SA",
        cnpj=None,
        banco="BANCO DO BRASIL",
        agencia="0001",
        conta="987654",
    ),
]


class MockClientService(ClientService):
    """Mock do serviço de clientes para testes."""
    
    def load_clients(self, force_reload: bool = False) -> list[ClientInfo]:
        return MOCK_CLIENTS


class TestMatchingService:
    """Testes do serviço de matching."""
    
    def setup_method(self):
        """Setup para cada teste."""
        self.service = MatchingService(client_service=MockClientService())
    
    def test_match_by_cnpj_exact(self):
        """Testa match por CNPJ exato."""
        extraction = LLMExtractionResult(
            cnpj="12.345.678/0001-90",
            banco="BRADESCO",
            tipo_documento="extrato bancário",
            confianca=0.9,
        )
        
        result = self.service.match(extraction)
        
        assert result.identificado
        assert result.cliente.cod == "098"
        assert result.cliente.nome == "JP CONTABIL LTDA"
        assert result.metodo == MatchMethod.CNPJ
        assert result.score == 100.0
    
    def test_match_by_cnpj_not_found(self):
        """Testa quando CNPJ não está cadastrado."""
        extraction = LLMExtractionResult(
            cnpj="99.999.999/0001-99",
            tipo_documento="extrato bancário",
            confianca=0.9,
        )
        
        result = self.service.match(extraction)
        
        assert not result.identificado
        assert result.metodo == MatchMethod.NAO_IDENTIFICADO
        assert "não encontrado" in result.motivo_fallback.lower() or "não cadastrado" in result.motivo_fallback.lower()
    
    def test_match_by_conta_agencia(self):
        """Testa match por agência e conta."""
        extraction = LLMExtractionResult(
            banco="ITAU",
            agencia="5678",
            conta="654321",
            tipo_documento="extrato bancário",
            confianca=0.8,
        )
        
        result = self.service.match(extraction)
        
        assert result.identificado
        assert result.cliente.cod == "089"
        assert result.metodo == MatchMethod.CONTA_AGENCIA
        assert result.score >= 90.0
    
    def test_match_by_name_similarity(self):
        """Testa match por similaridade de nome."""
        extraction = LLMExtractionResult(
            cliente_sugerido="JP CONTABIL",
            tipo_documento="extrato bancário",
            confianca=0.7,
        )
        
        result = self.service.match(extraction)
        
        assert result.identificado
        assert result.cliente.cod == "098"
        assert result.metodo == MatchMethod.NOME_SIMILARIDADE
        assert result.score >= 85
    
    def test_match_by_name_with_typo(self):
        """Testa match por nome com erro de digitação."""
        extraction = LLMExtractionResult(
            cliente_sugerido="IRMAOS ARALDI COMERCIO",
            tipo_documento="extrato bancário",
            confianca=0.6,
        )
        
        result = self.service.match(extraction)
        
        assert result.identificado
        assert result.cliente.cod == "089"
        assert result.metodo == MatchMethod.NOME_SIMILARIDADE
    
    def test_no_match_found(self):
        """Testa quando nenhum match é encontrado."""
        extraction = LLMExtractionResult(
            cliente_sugerido="EMPRESA DESCONHECIDA XYZ",
            tipo_documento="documento",
            confianca=0.3,
        )
        
        result = self.service.match(extraction)
        
        assert not result.identificado
        assert result.metodo == MatchMethod.NAO_IDENTIFICADO
        assert result.motivo_fallback is not None
    
    def test_priority_cnpj_over_name(self):
        """Testa que CNPJ tem prioridade sobre nome."""
        # Fornece nome de um cliente mas CNPJ de outro
        extraction = LLMExtractionResult(
            cliente_sugerido="IRMÃOS ARALDI",
            cnpj="12345678000190",  # CNPJ do JP CONTABIL
            tipo_documento="extrato bancário",
            confianca=0.9,
        )
        
        result = self.service.match(extraction)
        
        # Deve usar o match por CNPJ, não por nome
        assert result.identificado
        assert result.cliente.cod == "098"  # JP CONTABIL
        assert result.metodo == MatchMethod.CNPJ


class TestTextNormalization:
    """Testes de normalização de texto."""
    
    def test_extract_cnpj_formatted(self):
        """Testa extração de CNPJ formatado."""
        from app.utils.text import extract_cnpj
        
        text = "CNPJ: 12.345.678/0001-90"
        cnpj = extract_cnpj(text)
        
        assert cnpj == "12345678000190"
    
    def test_extract_cnpj_unformatted(self):
        """Testa extração de CNPJ sem formatação."""
        from app.utils.text import extract_cnpj
        
        text = "CNPJ 12345678000190 do cliente"
        cnpj = extract_cnpj(text)
        
        assert cnpj == "12345678000190"
    
    def test_normalize_text_accents(self):
        """Testa remoção de acentos."""
        from app.utils.text import normalize_text
        
        result = normalize_text("IRMÃOS ARALDI")
        
        assert result == "IRMAOS ARALDI"
    
    def test_extract_numbers(self):
        """Testa extração de números."""
        from app.utils.text import extract_numbers
        
        result = extract_numbers("Ag 1234-5 / Cc 67890-1")
        
        assert result == "123456789001"
