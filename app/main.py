import json
import datetime
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Body
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from app.database import get_db, engine
from app import models
from app.connection_manager import manager
from app.auth import (
    get_password_hash,
    authenticate_user,
    create_access_token,
    get_current_user
)

# Pydantic-модель для создания чата
class ChatCreate(BaseModel):
    name: str
    user_ids: list[int]

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Chat server is running"}

@app.post("/users/")
async def create_user(username: str, email: str, password: str, db: AsyncSession = Depends(get_db)):
    hashed_password = get_password_hash(password)
    new_user = models.User(username=username, email=email, password=hashed_password)
    db.add(new_user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    await db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username, "email": new_user.email}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/chats/")
async def create_chat(chat: ChatCreate, db: AsyncSession = Depends(get_db)):
    new_chat = models.Chat(name=chat.name, type=models.ChatType.group, creator_id=chat.user_ids[0])
    result = await db.execute(select(models.User).where(models.User.id.in_(chat.user_ids)))
    users = result.scalars().all()
    if not users:
        raise HTTPException(status_code=404, detail="Users not found")
    new_chat.users = users
    db.add(new_chat)
    await db.commit()
    await db.refresh(new_chat)
    return {"id": new_chat.id, "name": new_chat.name, "type": new_chat.type.value}

@app.get("/history/{chat_id}")
async def get_history(chat_id: int, limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.Message).where(models.Message.chat_id == chat_id)
        .order_by(models.Message.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()
    return [
        {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender_id,
            "text": msg.text,
            "created_at": msg.created_at.isoformat(),
            "is_read": msg.is_read
        }
        for msg in messages
    ]

@app.post("/messages/{message_id}/read")
async def mark_message_read(message_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Message).where(models.Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    message.is_read = True
    await db.commit()
    # Здесь можно добавить уведомление отправителя через WebSocket
    return {"message": "Message marked as read"}

@app.websocket("/ws/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, db: AsyncSession = Depends(get_db)):
    # Получение токена из query-параметров
    token = websocket.query_params.get("token")
    if token is None:
        await websocket.close(code=1008)
        return
    try:
        user = await get_current_user(token, db)
    except Exception:
        await websocket.close(code=1008)
        return
    user_id = user.id

    await manager.connect(chat_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid message format"})
                continue

            # Если сообщение содержит действие "read", обновляем статус сообщения
            if message_data.get("action") == "read":
                message_id = message_data.get("message_id")
                if message_id is None:
                    await websocket.send_json({"error": "Missing message_id for read action"})
                    continue
                result = await db.execute(select(models.Message).where(models.Message.id == message_id))
                message = result.scalar_one_or_none()
                if message:
                    message.is_read = True
                    try:
                        await db.commit()
                    except IntegrityError:
                        await db.rollback()
                        await websocket.send_json({"error": "Error updating message read status"})
                        continue
                    # Отправляем уведомление всем участникам (или только отправителю, если нужно)
                    notification = {"action": "read_update", "message_id": message_id, "is_read": True}
                    await manager.broadcast(chat_id, notification)
                continue  # Пропускаем дальнейшую обработку для этого цикла

            # Если обычное текстовое сообщение, обрабатываем его
            text = message_data.get("text")
            if not text:
                await websocket.send_json({"error": "No text provided"})
                continue

            current_time = time.time()
            if manager.is_duplicate(chat_id, user_id, text, current_time):
                await websocket.send_json({"error": "Duplicate message detected"})
                continue

            new_message = models.Message(
                chat_id=chat_id,
                sender_id=user_id,
                text=text,
                created_at=datetime.datetime.utcnow(),
                is_read=False
            )
            db.add(new_message)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                await websocket.send_json({"error": "Error saving message"})
                continue

            await db.refresh(new_message)
            message_to_send = {
                "id": new_message.id,
                "chat_id": chat_id,
                "sender_id": user_id,
                "text": text,
                "created_at": new_message.created_at.isoformat(),
                "is_read": new_message.is_read
            }
            await manager.broadcast(chat_id, message_to_send)
    except WebSocketDisconnect:
        manager.disconnect(chat_id, websocket)
