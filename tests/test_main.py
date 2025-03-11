import pytest_asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.database import engine, async_session
from app.models import Base, Message
from sqlalchemy.future import select


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
    response = await async_client.post("/users/", params={
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "testpassword"
    })
    assert response.status_code == 200
    data = response.json()
    assert "id" in data

    # Получение токена
    response = await async_client.post("/token", data={
        "username": "testuser",
        "password": "testpassword"
    })
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data

@pytest.mark.asyncio
async def test_create_chat(async_client: AsyncClient):
    # Создание двух пользователей
    response = await async_client.post("/users/", params={
        "username": "user1",
        "email": "user1@example.com",
        "password": "password1"
    })
    user1 = response.json()
    response = await async_client.post("/users/", params={
        "username": "user2",
        "email": "user2@example.com",
        "password": "password2"
    })
    user2 = response.json()

    # Создание группового чата
    response = await async_client.post("/chats/", json={
        "name": "Test Chat",
        "user_ids": [user1["id"], user2["id"]]
    })
    assert response.status_code == 200
    chat = response.json()
    assert "id" in chat


@pytest.mark.asyncio
async def test_get_history(async_client: AsyncClient):
    # Создание двух пользователей и чата
    response = await async_client.post("/users/", params={
        "username": "histuser1",
        "email": "histuser1@example.com",
        "password": "pass1"
    })
    user1 = response.json()
    response = await async_client.post("/users/", params={
        "username": "histuser2",
        "email": "histuser2@example.com",
        "password": "pass2"
    })
    user2 = response.json()

    response = await async_client.post("/chats/", json={
        "name": "History Chat",
        "user_ids": [user1["id"], user2["id"]]
    })
    chat = response.json()
    chat_id = chat["id"]

    # Вручную вставляем сообщение в чат через сессию БД
    async with async_session() as session:
        new_message = Message(
            chat_id=chat_id,
            sender_id=user1["id"],
            text="Test history message",
            is_read=False
        )
        session.add(new_message)
        await session.commit()
        await session.refresh(new_message)
        message_id = new_message.id

    # Получаем историю сообщений
    response = await async_client.get(f"/history/{chat_id}?limit=10&offset=0")
    assert response.status_code == 200
    history = response.json()
    # Проверяем, что найдено сообщение с ожидаемым текстом
    assert any(msg["id"] == message_id and msg["text"] == "Test history message" for msg in history)


@pytest.mark.asyncio
async def test_mark_message_read(async_client: AsyncClient):
    # Создание пользователя и чата
    response = await async_client.post("/users/", params={
        "username": "markuser",
        "email": "markuser@example.com",
        "password": "pass"
    })
    user = response.json()
    response = await async_client.post("/chats/", json={
        "name": "Mark Chat",
        "user_ids": [user["id"]]
    })
    chat = response.json()
    chat_id = chat["id"]

    # Вставляем сообщение вручную через БД
    async with async_session() as session:
        new_message = Message(
            chat_id=chat_id,
            sender_id=user["id"],
            text="Message to mark read",
            is_read=False
        )
        session.add(new_message)
        await session.commit()
        await session.refresh(new_message)
        message_id = new_message.id

    # Отправляем запрос на установку статуса "прочитано"
    response = await async_client.post(f"/messages/{message_id}/read")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Message marked as read"

    # Проверяем, что сообщение помечено как прочитанное в БД
    async with async_session() as session:
        result = await session.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        assert msg is not None and msg.is_read is True
