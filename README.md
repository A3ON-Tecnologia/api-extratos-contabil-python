# 📄 Extratos Contábeis - Sistema de Automação Documental

Sistema Python/FastAPI para processamento automático de extratos bancários e documentos contábeis com **monitoramento em tempo real**, **modo teste** e **gestão de reversões**.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o--mini-orange)

---

## 🚀 Funcionalidades

### Processamento de Documentos
- 📄 **Recebimento de arquivos** via HTTP POST (PDF ou ZIP)
- 🔍 **Extração de texto** de PDFs usando pdfplumber/PyPDF
- 🤖 **Análise via LLM** (OpenAI GPT-4o-mini) para identificar cliente, banco, período
- 🎯 **Matching inteligente** de clientes (CNPJ, conta/agência, similaridade de nome)
- 📁 **Organização automática** na estrutura de pastas em rede

### Monitoramento e Interface
- 📺 **Dashboard de Monitor** - Acompanhamento em tempo real via WebSocket
- 🧪 **Modo Teste** - Processa sem salvar arquivos (simulação)
- 🔄 **Módulo de Reversão** - Desfaz processamentos e deleta arquivos
- 📜 **Histórico de Reversões** - Log completo de todas as reversões

### Banco de Dados
- 💾 **SQLite** para persistência local
- 📊 **Logs de processamento** (produção e teste separados)
- 📋 **Logs de reversão** com snapshot dos dados

---

## 📁 Estrutura do Projeto

```
extratos-contabil-python/
├── app/
│   ├── main.py                    # API FastAPI principal
│   ├── config.py                  # Configurações e variáveis de ambiente
│   ├── database.py                # Configuração SQLAlchemy
│   ├── events.py                  # Sistema de eventos WebSocket
│   │
│   ├── models/                    # Modelos SQLAlchemy
│   │   ├── extrato_log.py         # Log de extratos processados
│   │   ├── extrato_log_teste.py   # Log de testes
│   │   └── reversao_log.py        # Log de reversões
│   │
│   ├── schemas/                   # Schemas Pydantic
│   │   ├── llm_response.py        # Retorno da LLM
│   │   ├── api.py                 # Request/Response da API
│   │   └── client.py              # Cliente e Match
│   │
│   ├── services/                  # Serviços de negócio
│   │   ├── pdf_service.py         # Extração de texto de PDF
│   │   ├── zip_service.py         # Extração de arquivos ZIP
│   │   ├── llm_service.py         # Integração com OpenAI
│   │   ├── client_service.py      # Leitura de planilha de clientes
│   │   ├── matching_service.py    # Matching de clientes
│   │   ├── storage_service.py     # Salvamento de arquivos
│   │   ├── audit_service.py       # Log em Excel
│   │   ├── db_log_service.py      # Log no banco de dados
│   │   ├── db_log_teste_service.py # Log de testes
│   │   └── reversao_service.py    # Gestão de reversões
│   │
│   ├── templates/                 # Templates HTML
│   │   ├── monitor.html           # Dashboard principal
│   │   ├── test_monitor.html      # Dashboard de testes
│   │   └── reversao.html          # Página de reversões
│   │
│   ├── static/                    # Arquivos estáticos
│   │   ├── css/
│   │   ├── js/
│   │   └── img/                   # Favicons
│   │
│   └── utils/                     # Utilitários
│       ├── hash.py                # Hash para idempotência
│       └── text.py                # Normalização de texto
│
├── scripts/                       # Scripts utilitários
│   ├── migrate_db.py              # Migração geral do banco
│   ├── migrate_reversoes_log.py   # Migração tabela reversões
│   └── ...
│
├── tests/                         # Testes automatizados
├── .env.example                   # Exemplo de configuração
├── pyproject.toml                 # Dependências Python
└── README.md
```

---

## 📦 Requirements

### Dependências Principais

```toml
# FastAPI e servidor
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.6

# Configuração
pydantic>=2.5.0
pydantic-settings>=2.1.0

# Banco de Dados
sqlalchemy>=2.0.0

# PDF
pypdf>=4.0.0
pdfplumber>=0.10.0

# LLM (OpenAI)
langchain>=0.1.0
langchain-openai>=0.0.5

# Excel
openpyxl>=3.1.0
pandas>=2.1.0

# Matching de texto
rapidfuzz>=3.6.0

# Logging
structlog>=24.1.0
```

### Dependências de Desenvolvimento

```toml
pytest>=7.4.0
pytest-asyncio>=0.23.0
httpx>=0.26.0
```

---

## 🛠️ Instalação

### 1. Clone o projeto

```bash
git clone <repo>
cd extratos-contabil-python
```

### 2. Crie um ambiente virtual

```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

### 3. Instale as dependências

```bash
pip install -e .
```

### 4. Configure as variáveis de ambiente

```bash
copy .env.example .env
# Edite o .env com suas configurações
```

### 5. Execute as migrações do banco

```bash
python scripts/migrate_db.py
```

---

## ⚙️ Configuração

Edite o arquivo `.env`:

```env
# API Key da OpenAI
OPENAI_API_KEY=sk-...

# Modelo LLM
LLM_MODEL=gpt-4o-mini

# Porta do servidor
PORT=8888

# Caminhos de rede
BASE_PATH=J:\JP Digital
CLIENTS_EXCEL_PATH=J:\JP Digital\000 - AUTOMAÇÕES\RELAÇÃO CLIENTES - CONTA _ AGÊNCIA.xlsx
LOG_EXCEL_PATH=J:\JP Digital\000 - AUTOMAÇÕES\LOGS SUCESSO _ FALHA.xlsx

# Pasta para não identificados
UNIDENTIFIED_PATH=J:\JP Digital\NAO_IDENTIFICADOS

# Banco de dados
DATABASE_URL=sqlite:///./extratos.db

# Threshold de similaridade para match por nome (0-100)
SIMILARITY_THRESHOLD=85
```

---

## 🚀 Uso

### Iniciar o servidor

```bash
uvicorn app.main:app --reload --port 8888
```

Ou execute diretamente:

```bash
python -m app.main
```

### Acessar as interfaces

| Interface | URL |
|-----------|-----|
| 📺 Monitor | http://localhost:8888/monitor |
| 🧪 Modo Teste | http://localhost:8888/test |
| 🔄 Reversão | http://localhost:8888/reversao |
| 📚 Swagger UI | http://localhost:8888/docs |
| 📖 ReDoc | http://localhost:8888/redoc |

---

## 🔌 Endpoints da API

### Processamento de Arquivos

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/upload` | Upload de PDF/ZIP para processamento |
| `GET` | `/job/{job_id}` | Status de um job específico |
| `GET` | `/jobs` | Lista todos os jobs recentes |

### Modo Teste

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/test/upload` | Upload para teste (não salva) |
| `GET` | `/test/job/{job_id}` | Status de job de teste |
| `GET` | `/test/logs` | Logs de teste |
| `GET` | `/test/stats` | Estatísticas de teste |
| `DELETE` | `/test/logs` | Limpar logs de teste |

### Reversão

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/reversao/listar` | Lista processamentos para reverter |
| `DELETE` | `/reversao/{id}` | Reverte um processamento |
| `POST` | `/reversao/lote` | Reverte múltiplos processamentos |
| `GET` | `/reversao/historico` | Histórico de reversões |
| `GET` | `/reversao/stats` | Estatísticas |

### Logs do Banco

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/logs` | Lista logs de processamento |
| `GET` | `/logs/stats` | Estatísticas dos logs |
| `GET` | `/logs/{id}` | Detalhes de um log |

### Utilitários

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/health` | Health check |
| `POST` | `/reload-clients` | Recarrega planilha de clientes |
| `POST` | `/reload-settings` | Recarrega configurações |
| `POST` | `/monitor/reset` | Para processamentos em andamento |

---

## 🧠 Arquitetura e Lógicas

### Fluxo de Processamento

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Upload    │ ──▶ │  Extração   │ ──▶ │  Análise    │ ──▶ │  Matching   │
│  PDF/ZIP    │     │   Texto     │     │    LLM      │     │  Cliente    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                                                   │
                                                                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   WebSocket │ ◀── │    Log      │ ◀── │  Arquivo    │ ◀── │  Caminho    │
│   Eventos   │     │   Banco     │     │   Salvo     │     │  Destino    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Serviços e Responsabilidades

#### 📄 PDFService
- Extração de texto usando **pdfplumber** (preferencial)
- Fallback para **PyPDF** em caso de erro
- Suporte a PDFs com múltiplas páginas

#### 🤖 LLMService
- Integração com **OpenAI GPT-4o-mini** via LangChain
- Prompt otimizado para documentos financeiros
- Classificação automática de tipo de documento:
  - `CC` - Conta Corrente
  - `POUPANCA` - Poupança
  - `INVESTIMENTO` - Investimentos, CDB, Fundos
  - `CARTAO_CREDITO` - Cartões, Maquininhas
  - `EMPRESTIMO` - Empréstimos, Financiamentos
  - `PIX` - Transferências PIX
  - `PAGAMENTOS` - Comprovantes de pagamento
  - E outros...

#### 🎯 MatchingService
Ordem de prioridade para identificação:
1. **CNPJ exato** - Match direto pelo CNPJ
2. **Agência + Conta + Banco** - Combinação na planilha
3. **Nome com similaridade** - RapidFuzz com threshold

#### 📁 StorageService
- Organização automática em estrutura de pastas
- Nomenclatura padronizada: `TIPO_BANCO.pdf`
- Tratamento de duplicatas
- Mês anterior automático

#### 🔄 ReversaoService
- Desfaz processamentos
- Deleta arquivos do disco
- Registra histórico completo
- Suporte a reversão em lote

---

## 🗄️ Banco de Dados

### Tabelas

| Tabela | Descrição |
|--------|-----------|
| `extratos_log` | Logs de processamento de produção |
| `extratos_log_teste` | Logs de processamento de teste |
| `reversoes_log` | Histórico de reversões |

### Migrações

```bash
# Migração geral (cria todas as tabelas)
python scripts/migrate_db.py

# Migração específica da tabela de reversões
python scripts/migrate_reversoes_log.py
```

---

## 📊 Planilhas

### Planilha de Clientes

Colunas obrigatórias:

| COD | NOME | CNPJ | BANCO | AGENCIA | Nº CONTA |
|-----|------|------|-------|---------|----------|
| 098 | JP CONTABIL LTDA | 12.345.678/0001-90 | BRADESCO | 1234 | 12345-6 |

### Planilha de LOG

Registro automático:

| DATA | NOME DO CLIENTE | TIPO EXTRATO | ANO | MÊS | STATUS | NOME ARQUIVO FINAL |
|------|-----------------|--------------|-----|-----|--------|-------------------|
| 13/01/2024 14:30:00 | JP CONTABIL LTDA | CC | 2024 | 12 | SUCESSO | J:\...\CC_BRADESCO.pdf |

---

## 📂 Estrutura de Pastas

```
J:\JP Digital\
├── 098 - JP CONTABIL LTDA\
│   └── Departamento Contabil\
│       └── 2024\
│           └── DEZEMBRO\
│               └── CC_BRADESCO.pdf
│
├── NAO_IDENTIFICADOS\
│   └── 2024\
│       └── DEZEMBRO\
│           └── documento_original.pdf
│
└── 000 - AUTOMAÇÕES\
    ├── RELAÇÃO CLIENTES - CONTA _ AGÊNCIA.xlsx
    └── LOGS SUCESSO _ FALHA.xlsx
```

---

## 🔗 Integração com Make/Zapier

No Make, configure um módulo HTTP:

- **URL**: `http://seu-servidor:8888/upload`
- **Method**: `POST`
- **Body type**: `Multipart/form-data`
- **Field name**: `file`
- **File**: Arquivo do módulo de e-mail

---

## 🧪 Testes

```bash
# Instalar dependências de desenvolvimento
pip install -e ".[dev]"

# Executar testes
pytest

# Com cobertura
pytest --cov=app
```

---

## 📝 Licença

Uso interno.

---

## 🛠️ Desenvolvido por

A3ON Tecnologia
