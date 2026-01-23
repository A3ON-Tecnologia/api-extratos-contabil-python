"""
Configuração do banco de dados com SQLAlchemy.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    Gera uma sessão do banco de dados.
    
    Uso:
        with get_db() as db:
            db.query(...)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Inicializa o banco de dados criando as tabelas.
    
    Deve ser chamado na inicialização da aplicação.
    """
    from app.models.extrato_log import ExtratoLog  # noqa
    from app.models.extrato_log_teste import ExtratoLogTeste  # noqa
    from app.models.extratos_baixados_log import ExtratosBaixadosLog  # noqa
    from app.models.extratos_baixados_log_teste import ExtratosBaixadosLogTeste  # noqa
    from app.models.extratos_baixados_reversao_log import ExtratosBaixadosReversaoLog  # noqa
    from app.models.reversao_log import ReversaoLog  # noqa
    Base.metadata.create_all(bind=engine)
