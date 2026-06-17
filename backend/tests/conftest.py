"""Phase 1 테스트 인프라.

fixture chain:
  postgres_container (session) → fresh_db (function: CREATE/DROP DB)
    → alembic_config → upgraded_db (alembic upgrade head + monkey-patch settings)
      → async_session (asyncpg AsyncSession)

Per-test fresh DB; teardown terminates connections then DROPs. Not pytest-xdist safe.
"""

import logging
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://placeholder/pslog_test")
os.environ.setdefault("SECRET_KEY", "test-secret-not-used-by-tests")

import pytest
import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

# Patch app.config.settings at import time so env.py picks up real URLs later
import app.config  # noqa: E402 — ensures settings singleton is in sys.modules


def _reenable_app_loggers() -> None:
    """alembic.ini의 fileConfig(disable_existing_loggers=True) 실행 후
    'app.*' 로거의 disabled 플래그를 False 로 복원.

    alembic upgrade 는 alembic.ini [loggers] 에 없는 기존 로거를 disabled=True 로 설정.
    테스트에서 app.* 로거가 살아있어야 caplog 캡처가 동작함.
    """
    manager = logging.root.manager
    for name, logger_or_placeholder in manager.loggerDict.items():
        if name.startswith("app"):
            if isinstance(logger_or_placeholder, logging.Logger):
                logger_or_placeholder.disabled = False


# ---------------------------------------------------------------------------
# pytest-asyncio + caplog 호환성 픽스처
# pytest-asyncio 0.24 에서 async 픽스처를 사용하는 테스트는
# caplog.handler 가 root logger 에 설치되지 않는 문제가 있음.
# 'app' 로거에 caplog.handler 를 직접 설치해 app 내부 로그를 caplog 에 캡처.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _install_caplog_handler_on_app_logger(caplog: pytest.LogCaptureFixture):
    """async 테스트에서 caplog 캡처가 동작하도록 'app' logger 에 handler 직접 설치.

    pytest-asyncio 0.24 에서 caplog.handler 가 root 에 설치되지 않는 문제 회피.
    """
    app_logger = logging.getLogger("app")
    app_logger.addHandler(caplog.handler)
    original_level = app_logger.level
    if original_level == logging.NOTSET or original_level > logging.DEBUG:
        app_logger.setLevel(logging.DEBUG)
    yield
    app_logger.removeHandler(caplog.handler)
    app_logger.setLevel(original_level)

# ---------------------------------------------------------------------------
# Session-scoped: one PG container for the entire test run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


# ---------------------------------------------------------------------------
# Function-scoped: fresh database per test
# ---------------------------------------------------------------------------

def _admin_dsn(pg: PostgresContainer) -> str:
    """Return a psycopg (sync) DSN pointing at the container's default DB."""
    raw = pg.get_connection_url()  # postgresql+psycopg2://...
    assert raw.startswith("postgresql+psycopg2://"), (
        f"testcontainers returned unexpected URL prefix: {raw!r}. "
        "If testcontainers changed the driver, update _admin_dsn accordingly."
    )
    return raw.replace("postgresql+psycopg2://", "postgresql://")


def _db_dsn_sync(pg: PostgresContainer, db_name: str) -> str:
    """psycopg (sync) DSN for a specific DB — used by alembic."""
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    user = pg.username
    password = pg.password
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"


def _db_dsn_async(pg: PostgresContainer, db_name: str) -> str:
    """asyncpg DSN for a specific DB — used by AsyncSession."""
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    user = pg.username
    password = pg.password
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"


@pytest.fixture()
def fresh_db(postgres_container):
    """Create an isolated DB for one test, drop it on teardown."""
    db_name = f"test_{uuid.uuid4().hex[:12]}"
    admin_dsn = _admin_dsn(postgres_container)

    # CREATE DATABASE requires autocommit — psycopg 3 uses autocommit=True
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')

    yield db_name

    # Terminate any open connections before dropping
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        conn.execute(f'DROP DATABASE "{db_name}"')


# ---------------------------------------------------------------------------
# Function-scoped: alembic config pointing at the fresh DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def alembic_config(postgres_container, fresh_db):
    """Alembic Config object wired to the per-test database."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", _db_dsn_sync(postgres_container, fresh_db))
    return cfg


# ---------------------------------------------------------------------------
# Function-scoped: run alembic upgrade head, yield DSN info
# ---------------------------------------------------------------------------

@pytest.fixture()
def upgraded_db(alembic_config, postgres_container, fresh_db):
    """Run alembic upgrade head and yield a dict with connection details.

    env.py does `config.set_main_option("sqlalchemy.url", get_async_url(settings.database_url))`
    at module load, overwriting the URL we set in alembic_config.  We patch
    settings.database_url so env.py sees the real per-test DB URL.
    """
    async_url = _db_dsn_async(postgres_container, fresh_db)

    # Patch the module-level settings singleton so env.py's line-29 override
    # produces the correct per-test async URL instead of the placeholder.
    original_db_url = app.config.settings.database_url
    app.config.settings.database_url = async_url

    try:
        command.upgrade(alembic_config, "head")
    finally:
        app.config.settings.database_url = original_db_url
        # alembic.ini 의 fileConfig 가 기존 로거를 disable 함 — app 로거 복원
        _reenable_app_loggers()

    yield {
        "db_name": fresh_db,
        "sync_url": _db_dsn_sync(postgres_container, fresh_db),
        "async_url": async_url,
    }


# ---------------------------------------------------------------------------
# Function-scoped async: AsyncSession bound to the upgraded DB
# ---------------------------------------------------------------------------

@pytest.fixture()
async def async_session(upgraded_db):
    """Yield an AsyncSession connected to the per-test alembic-migrated DB."""
    engine = create_async_engine(upgraded_db["async_url"], echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()
