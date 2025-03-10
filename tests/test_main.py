import pytest_asyncio
import pytest
import anyio
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.database import engine
from app.models import Base


@pytest_asyncio.fixture(scope="function")
async def async_client():
    # Подготовка тестовой базы: очистка и создание таблиц
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # Создание клиента для REST‑тестирования через ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_create_user_and_get_token(async_client: AsyncClient):
    # Создание пользователя
    response = await async_client.post("/users/", params={"username": "testuser", "password": "testpassword"})
    assert response.status_code == 200
    data = response.json()
    assert "id" in data

    # Получение токена
    response = await async_client.post("/token", data={"username": "testuser", "password": "testpassword"})
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data

@pytest.mark.asyncio
async def test_create_chat(async_client: AsyncClient):
    # Создание двух пользователей
    response = await async_client.post("/users/", params={"username": "user1", "password": "password1"})
    user1 = response.json()
    response = await async_client.post("/users/", params={"username": "user2", "password": "password2"})
    user2 = response.json()

    # Создание группового чата (входные данные теперь валидируются через Pydantic-модель)
    response = await async_client.post("/chats/", json={"name": "Test Chat", "user_ids": [user1["id"], user2["id"]]})
    assert response.status_code == 200
    chat = response.json()
    assert "id" in chat
