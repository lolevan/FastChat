import os
import pytest
import asyncio

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:password@localhost:5432/postgres"


@pytest.fixture(scope="module")
def event_loop():
    """Создаём event loop для тестов с scope=module"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
