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
    
    def match(self, extraction: LLMExtractionResult, is_ofx: bool = False) -> MatchResult:
        """
        Tenta identificar o cliente com base nas informações extraídas.

        Ordem de prioridade para arquivos OFX:
        1. Match por Conta (primeiro critério para OFX)
        2. Match por CNPJ exato
        3. Match por similaridade de nome

        Ordem de prioridade para outros arquivos:
        1. Match por CNPJ exato
        2. Match por Agência + Conta + Banco
        3. Match por similaridade de nome

        Args:
            extraction: Resultado da extração da LLM
            is_ofx: Se True, prioriza busca por conta (padrão: False)

        Returns:
            Resultado do matching com cliente (se encontrado) e metadados
        """
        # Para OFX, usa planilha definida em EXTRATOS_EXCEL_PATH (config .env)
        if is_ofx:
            try:
                clients = self.client_service.load_clients_from_path(
                    self.settings.extratos_excel_path,
                    force_reload=True,
                )
                logger.info("[OFX] Planilha usada: %s", self.settings.extratos_excel_path)
            except Exception as e:
                logger.warning("[OFX] Falha ao carregar planilha de extratos: %s", e)
                clients = self.client_service.load_clients()
        else:
            clients = self.client_service.load_clients()

        clients_extratos = None
        if extraction.conta:
            try:
                clients_extratos = self.client_service.load_clients_from_path(
                    self.settings.extratos_excel_path,
                    force_reload=True,
                )
                logger.info("[CONTA] Planilha usada: %s", self.settings.extratos_excel_path)
            except Exception as e:
                logger.warning("[CONTA] Falha ao carregar planilha de extratos: %s", e)
                clients_extratos = None

        if extraction.conta:
            base_clients = clients_extratos or clients
            result = self._match_by_conta_exata(
                extraction.conta,
                extraction.tipo_documento,
                base_clients,
            )
            if result.identificado:
                logger.info("Match por CONTA EXATA: %s", result.cliente.nome)
                return result

        # Para arquivos OFX: IDENTIFICA??O APENAS POR CONTA
        if is_ofx and extraction.conta:
            logger.info("[OFX] Buscando por CONTA (sem exigir ag?ncia)")
            result = self._match_by_conta_only(
                extraction.banco,
                extraction.conta,
                clients
            )

            if result.identificado:
                logger.info(f"[OFX] Match por Conta: {result.cliente.nome}")
                return result

        # 1. Tentar match por CNPJ
        if extraction.cnpj:
            result = self._match_by_cnpj(
                extraction.cnpj,
                clients,
                banco=extraction.banco,
                conta=extraction.conta,
            )
            if result.identificado:
                logger.info(f"Match por CNPJ: {result.cliente.nome}")
                return result

        # 2. Tentar match por Agência + Conta + Banco (para não-OFX ou quando conta OFX falhou)
        if extraction.agencia and extraction.conta and not is_ofx:
            # Conta Capital: busca apenas por banco + agência (conta capital tem número diferente)
            is_conta_capital = extraction.tipo_documento and "CONTA CAPITAL" in extraction.tipo_documento.upper()

            if is_conta_capital:
                logger.info("Extrato de CONTA CAPITAL detectado - matching apenas por banco+agência")
                result = self._match_by_agencia_only(
                    extraction.banco,
                    extraction.agencia,
                    clients
                )
            else:
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
            result = self._match_by_name(
                extraction.cliente_sugerido,
                clients,
                banco=extraction.banco,
                agencia=extraction.agencia,
                conta=extraction.conta,
            )
            if result.identificado:
                logger.info(
                    f"Match por nome ({result.score:.1f}%): {result.cliente.nome}"
                )
                return result
        
        # Nenhum match encontrado
        motivo = self._build_fallback_reason(extraction, is_ofx=is_ofx)
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
        clients: list[ClientInfo],
        banco: str | None = None,
        conta: str | None = None,
    ) -> MatchResult:
        """
        Tenta encontrar cliente pelo CNPJ.

        Match exato considerando apenas os números.
        Para conta, tenta match com e sem dígito verificador.
        """
        cnpj_numbers = extract_numbers(cnpj)

        if len(cnpj_numbers) != 14:
            return MatchResult(
                motivo_fallback=f"CNPJ inválido: {cnpj}"
            )

        banco_cresol = self._is_cresol(banco)
        conta_exata = None
        if conta:
            conta_exata = self._normalize_conta_exata(conta)

        for client in clients:
            if not client.cnpj:
                continue

            client_cnpj = extract_numbers(client.cnpj)
            if client_cnpj == cnpj_numbers:
                # Verifica banco CRESOL
                if banco_cresol:
                    if not client.banco:
                        continue
                    client_banco = normalize_text(client.banco)
                    if "CRESOL" not in client_banco:
                        continue

                # SEMPRE verifica conta se fornecida (não só para CRESOL!)
                # Importante: mesmo cliente pode ter várias contas em bancos diferentes
                if conta_exata and client.conta:
                    # Match com ou sem verificador
                    client_conta_exata = self._normalize_conta_exata(str(client.conta))
                    match_conta = (client_conta_exata == conta_exata)
                    if not match_conta:
                        continue  # Pula para próxima linha do mesmo cliente
                elif conta_exata and not client.conta:
                    # Se temos conta extraída mas cliente não tem conta cadastrada, skip
                    continue

                return MatchResult(
                    cliente=client,
                    metodo=MatchMethod.CNPJ,
                    score=100.0,
                )

        return MatchResult(
            motivo_fallback=f"CNPJ não encontrado na base: {cnpj}"
        )
    
    def _normalize_agencia(self, agencia_value: str) -> str:
        """
        Normaliza agência removendo dígitos verificadores e datas incorretas.

        Exemplos:
        - "3037-6" → "3037"
        - "3037-06-01 00:00:00" → "3037" (data do Excel)
        - "5684" → "5684"
        """
        if not agencia_value:
            return "0"

        agencia_str = str(agencia_value)

        # Se tem hífen, pega apenas a parte antes do primeiro hífen
        if "-" in agencia_str:
            agencia_str = agencia_str.split("-")[0].strip()

        # Remove não-dígitos e zeros à esquerda
        numbers = extract_numbers(agencia_str).lstrip("0") or "0"
        return numbers

    def _match_by_agencia_only(
        self,
        banco: str | None,
        agencia: str,
        clients: list[ClientInfo]
    ) -> MatchResult:
        """
        Match apenas por Banco + Agência (usado para Conta Capital).

        Conta Capital tem número diferente da conta corrente,
        então ignoramos a conta e buscamos apenas por banco + agência.
        """
        agencia_numbers = self._normalize_agencia(agencia)
        banco_normalized = normalize_text(banco) if banco else None

        candidates: list[tuple[ClientInfo, float]] = []

        for client in clients:
            if not client.agencia:
                continue

            # Normaliza agência do cliente (com suporte a datas do Excel)
            client_agencia = self._normalize_agencia(str(client.agencia))

            # Verifica se agência bate
            if client_agencia != agencia_numbers:
                continue

            # Se chegou aqui, agência bate
            score = 80.0  # Score menor pois não validamos conta

            # Bonus se o banco também bater (matching flexível)
            if banco_normalized and client.banco:
                client_banco = normalize_text(client.banco)
                # Remove palavra "BANCO" para matching flexível
                banco_norm_clean = banco_normalized.replace("BANCO", "").strip()
                client_banco_clean = client_banco.replace("BANCO", "").strip()

                if (banco_normalized in client_banco or
                    client_banco in banco_normalized or
                    banco_norm_clean in client_banco_clean or
                    client_banco_clean in banco_norm_clean):
                    score = 85.0

            candidates.append((client, score))

        # Se há múltiplos candidatos, retorna o primeiro
        # (pode melhorar isso no futuro perguntando ao usuário)
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_client, best_score = candidates[0]

            logger.warning(
                f"Match por agência apenas (Conta Capital): {best_client.nome}. "
                f"{'Múltiplos clientes encontrados!' if len(candidates) > 1 else ''}"
            )

            return MatchResult(
                cliente=best_client,
                metodo=MatchMethod.CONTA_AGENCIA,  # Reutiliza o mesmo método
                score=best_score,
            )

        return MatchResult(
            motivo_fallback=f"Agência não encontrada: Ag {agencia}"
        )

    def _normalize_conta_para_matching(self, conta: str) -> tuple[str, str]:
        """
        Normaliza conta para matching, retornando duas versões:
        1. Conta completa (com dígito verificador)
        2. Conta sem último dígito (sem verificador)

        Returns:
            Tupla (conta_exata, conta_sem_verificador)
        """
        conta_numbers = extract_numbers(conta)
        # Remove o último dígito (verificador) se tiver mais de 1 dígito
        conta_sem_verificador = conta_numbers[:-1] if len(conta_numbers) > 1 else conta_numbers
        return (conta_numbers, conta_sem_verificador)

    def _normalize_conta_exata(self, conta: str) -> str:
        """Normaliza conta mantendo zeros a esquerda (match exato)."""
        return extract_numbers(conta)

    def _tipo_documento_compat(self, tipo_extrato: str | None, tipo_cliente: str | None) -> bool:
        if not tipo_extrato or not tipo_cliente:
            return True
        tipo_norm = normalize_text(tipo_extrato)
        cliente_norm = normalize_text(tipo_cliente)
        return tipo_norm == cliente_norm or tipo_norm in cliente_norm or cliente_norm in tipo_norm

    def _match_by_conta_exata(self, conta: str, tipo_documento: str | None, clients: list[ClientInfo]) -> MatchResult:
        """Match estrito por conta, com filtro opcional por tipo de documento."""
        conta_exata = self._normalize_conta_exata(conta)
        if not conta_exata:
            return MatchResult(motivo_fallback="Conta vazia para matching exato")

        candidates: list[ClientInfo] = []
        candidates_tipo: list[ClientInfo] = []

        for client in clients:
            if not client.conta:
                continue
            client_conta = self._normalize_conta_exata(str(client.conta))
            if client_conta != conta_exata:
                continue
            candidates.append(client)
            if self._tipo_documento_compat(tipo_documento, client.tipo_documento):
                candidates_tipo.append(client)

        if tipo_documento and candidates_tipo:
            return MatchResult(cliente=candidates_tipo[0], metodo=MatchMethod.CONTA_AGENCIA, score=100.0)
        if candidates:
            return MatchResult(cliente=candidates[0], metodo=MatchMethod.CONTA_AGENCIA, score=100.0)

        return MatchResult(motivo_fallback=f"Conta nao encontrada (match exato): {conta}")

    def _match_by_conta_only(
        self,
        banco: str | None,
        conta: str,
        clients: list[ClientInfo],
    ) -> MatchResult:
        """
        Match apenas por Conta (usado para OFX sem ag??ncia), com match exato.
        """
        conta_exata = self._normalize_conta_exata(conta)
        banco_normalized = normalize_text(banco) if banco else None
        banco_cresol = self._is_cresol(banco)

        candidates: list[tuple[ClientInfo, float]] = []

        for client in clients:
            if not client.conta:
                continue
            if banco_cresol:
                if not client.banco:
                    continue
                if "CRESOL" not in normalize_text(client.banco):
                    continue

            client_conta = self._normalize_conta_exata(str(client.conta))
            if client_conta != conta_exata:
                continue

            score = 75.0
            if banco_normalized and client.banco:
                client_banco = normalize_text(client.banco)
                banco_norm_clean = banco_normalized.replace("BANCO", "").strip()
                client_banco_clean = client_banco.replace("BANCO", "").strip()
                if (banco_normalized in client_banco or
                    client_banco in banco_normalized or
                    banco_norm_clean in client_banco_clean or
                    client_banco_clean in banco_norm_clean):
                    score += 10.0

            candidates.append((client, score))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_client, best_score = candidates[0]
            logger.warning(
                f"Match por conta apenas (OFX sem ag??ncia): {best_client.nome}. "
                f"{'M??ltiplos clientes encontrados!' if len(candidates) > 1 else ''}"
            )
            return MatchResult(
                cliente=best_client,
                metodo=MatchMethod.CONTA_AGENCIA,
                score=best_score,
            )

        return MatchResult(
            motivo_fallback=f"Conta n??o encontrada: Cc {conta}"
        )

    def _match_by_conta(
        self,
        banco: str | None,
        agencia: str,
        conta: str,
        clients: list[ClientInfo]
    ) -> MatchResult:
        """
        Tenta encontrar cliente pela combina????o Banco + Ag??ncia + Conta (match exato).
        """
        agencia_numbers = extract_numbers(agencia).lstrip("0") or "0"
        conta_exata = self._normalize_conta_exata(conta)
        banco_normalized = normalize_text(banco) if banco else None
        banco_cresol = self._is_cresol(banco)

        candidates: list[tuple[ClientInfo, float]] = []

        for client in clients:
            if not client.agencia or not client.conta:
                continue
            if banco_cresol:
                if not client.banco:
                    continue
                if "CRESOL" not in normalize_text(client.banco):
                    continue

            client_agencia = extract_numbers(str(client.agencia)).lstrip("0") or "0"
            if client_agencia != agencia_numbers:
                continue

            client_conta = self._normalize_conta_exata(str(client.conta))
            if client_conta != conta_exata:
                continue

            score = 92.0
            if banco_normalized and client.banco:
                client_banco = normalize_text(client.banco)
                if banco_cresol and "CRESOL" not in client_banco:
                    continue
                if banco_normalized in client_banco or client_banco in banco_normalized:
                    score += 3.0

            candidates.append((client, score))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_client, best_score = candidates[0]

            return MatchResult(
                cliente=best_client,
                metodo=MatchMethod.CONTA_AGENCIA,
                score=best_score,
            )

        return MatchResult(
            motivo_fallback=f"Conta/Ag??ncia n??o encontrada: Ag {agencia} / Cc {conta}"
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
        cleaned = re.sub(r'^[\d\.\-\/]+\s+', '', cleaned)
        
        return cleaned.strip()

    def _strip_generic_suffix(self, name: str) -> str:
        """
        Remove sufixos genéricos que causam falso positivo no matching.
        Ex: 'EMPRESA X MATERIAIS PARA CONSTRUCAO LTDA' -> 'EMPRESA X'
        """
        import re

        if not name:
            return name

        normalized = name.upper().strip()

        # Remove sufixos legais comuns no fim
        normalized = re.sub(r'\b(LTDA|LTDA\.|ME|EPP|EIRELI|S\/A|SA)\b\s*$', '', normalized).strip()

        # Remove frase genérica "MATERIAIS PARA CONSTRUCAO" no fim
        normalized = re.sub(r'\bMATERIAIS?\s+PARA\s+CONSTRUCAO\b\s*$', '', normalized).strip()

        return normalized.strip()

    def _requires_conta_agencia_confirmation(self, name: str) -> bool:
        """
        Detecta nomes gen??ricos ligados a materiais/constru????o que
        exigem confirma????o por conta+ag??ncia para evitar falso positivo.
        """
        import re

        if not name:
            return False

        normalized = normalize_text(name)
        patterns = [
            r"\bMATERIAIS\b",
            r"\bMATERIAL\s+DE\s+CONSTRUCAO\b",
            r"\bCONSTRUCAO\b",
        ]
        return any(re.search(pattern, normalized) for pattern in patterns)

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
        clients: list[ClientInfo],
        banco: str | None = None,
        agencia: str | None = None,
        conta: str | None = None,
    ) -> MatchResult:
        """
        Tenta encontrar cliente por similaridade de nome.
        
        Utiliza rapidfuzz com threshold configurável.
        """
        # Para nomes gen??ricos (materiais/constru????o), exige confirma????o por conta+ag??ncia
        if self._requires_conta_agencia_confirmation(nome_sugerido):
            if not agencia or not conta:
                return MatchResult(
                    motivo_fallback=(
                        "Nome gen??rico (materiais/constru????o) sem ag??ncia/conta para confirmar"
                    )
                )
            conta_match = self._match_by_conta(banco, agencia, conta, clients)
            if conta_match.identificado:
                return conta_match
            return MatchResult(
                motivo_fallback=(
                    "Nome gen??rico (materiais/constru????o) exige conta+ag??ncia compat??veis"
                )
            )

        # Limpa o nome sugerido antes de normalizar
        nome_limpo = self._clean_company_name(nome_sugerido)
        nome_base = self._strip_generic_suffix(nome_limpo)
        if len(nome_base) < 3:
            nome_base = nome_limpo

        nome_normalized = normalize_text(nome_base)
        nome_abbr = normalize_text(self._apply_abbreviations(nome_base))
        
        threshold = self.settings.similarity_threshold
        
        best_match: tuple[ClientInfo, float] | None = None
        banco_cresol = self._is_cresol(banco)
        conta_exata = None
        if conta:
            conta_exata = self._normalize_conta_exata(conta)

        def get_max_score(target_name: str, candidate_name: str) -> float:
            return max([
                fuzz.ratio(target_name, candidate_name),
                fuzz.partial_ratio(target_name, candidate_name),
                fuzz.token_sort_ratio(target_name, candidate_name),
                fuzz.token_set_ratio(target_name, candidate_name),
                fuzz.WRatio(target_name, candidate_name),
            ])

        def find_best_match(require_conta_match: bool) -> tuple[ClientInfo, float] | None:
            best: tuple[ClientInfo, float] | None = None
            for client in clients:
                if banco_cresol:
                    if not client.banco:
                        continue
                    client_banco = normalize_text(client.banco)
                    if "CRESOL" not in client_banco:
                        continue
                    if require_conta_match and conta_exata:
                        if not client.conta:
                            continue
                            # Match com ou sem verificador
                        client_conta_exata = self._normalize_conta_exata(str(client.conta))
                        match_conta = (client_conta_exata == conta_exata)
                        if not match_conta:
                            continue

                client_nome_raw = client.nome or ""
                client_base = self._strip_generic_suffix(client_nome_raw)
                if len(client_base) < 3:
                    client_base = client_nome_raw
                client_nome = normalize_text(client_base)

                score_normal = get_max_score(nome_normalized, client_nome)
                score_abbr = get_max_score(nome_abbr, client_nome)
                score = max(score_normal, score_abbr)

                if score >= threshold:
                    if best is None or score > best[1]:
                        best = (client, score)
            return best

        # Primeiro tenta nome com banco+conta (quando aplicavel) para evitar falso positivo
        best_match = find_best_match(require_conta_match=True)

        # Se nao encontrou e e Cresol, tenta apenas nome+banco (conta pode estar ausente na planilha)
        # IMPORTANTE: Banco Cresol frequentemente não possui conta cadastrada na planilha de clientes.
        # Quando temos conta_exata extraída do documento mas não há match com conta+nome, fazemos
        # uma segunda tentativa apenas com nome+banco, permitindo identificação mesmo sem conta.
        # Corrigido: usava 'conta_numbers' (inexistente) -> agora usa 'conta_exata' (correto)
        if best_match is None and banco_cresol and conta_exata:
            best_match = find_best_match(require_conta_match=False)

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
    
    def _is_cresol(self, banco: str | None) -> bool:
        """Verifica se o banco e Cresol."""
        if not banco:
            return False
        return "CRESOL" in normalize_text(banco)

    def _build_fallback_reason(self, extraction: LLMExtractionResult, is_ofx: bool = False) -> str:
        """Constr?i uma mensagem explicando por que n?o foi poss?vel identificar."""
        reasons = []

        if is_ofx:
            if not extraction.conta:
                reasons.append("Conta n?o identificada no OFX")
            else:
                reasons.append(f"Conta {extraction.conta} n?o cadastrada")
        else:
            if not extraction.cnpj:
                reasons.append("CNPJ n?o encontrado no documento")
            else:
                reasons.append(f"CNPJ {extraction.cnpj} n?o cadastrado")

            if not extraction.agencia or not extraction.conta:
                reasons.append("Ag?ncia/Conta n?o identificadas")
            else:
                reasons.append(
                    f"Ag {extraction.agencia} / Cc {extraction.conta} n?o cadastradas"
                )

        if not extraction.cliente_sugerido:
            reasons.append("Nome do cliente n?o identificado")
        else:
            reasons.append(
                f"Nome '{extraction.cliente_sugerido}' sem match suficiente"
            )

        return "; ".join(reasons)
