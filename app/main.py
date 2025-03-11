import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Body
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
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
from app.services import UserService, ChatService, MessageService


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
async def create_user(username: str, email: str, password: str, db=Depends(get_db)):
    user_service = UserService()
    try:
        user = await user_service.create_user(db, username, email, get_password_hash(password))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": user.id, "username": user.username, "email": user.email}


@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/chats/")
async def create_chat(chat: ChatCreate, db=Depends(get_db)):
    chat_service = ChatService()
    try:
        # Используем первого пользователя из списка как создателя
        new_chat = await chat_service.create_chat(db, chat.name, chat.user_ids, creator_id=chat.user_ids[0])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": new_chat.id, "name": new_chat.name, "type": new_chat.type.value}


@app.get("/history/{chat_id}")
async def get_history(chat_id: int, limit: int = 50, offset: int = 0, db=Depends(get_db)):
    chat_service = ChatService()
    messages = await chat_service.get_history(db, chat_id, limit, offset)
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
async def mark_message_read(message_id: int, db=Depends(get_db)):
    message_service = MessageService()
    try:
        await message_service.mark_read(db, message_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "Message marked as read"}


@app.websocket("/ws/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, db=Depends(get_db)):
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

    # Проверка, является ли пользователь участником чата:
    result = await db.execute(
        select(models.Chat)
        .options(selectinload(models.Chat.users))
        .where(models.Chat.id == chat_id)
    )
    chat = result.scalar_one_or_none()
    if not chat or user_id not in [u.id for u in chat.users]:
        await websocket.close(code=1008)
        return

    await manager.connect(chat_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid message format"})
                continue

            # Обработка события прочтения
            if message_data.get("action") == "read":
                message_id = message_data.get("message_id")
                if message_id is None:
                    await websocket.send_json({"error": "Missing message_id for read action"})
                    continue
                message_service = MessageService()
                try:
                    await message_service.mark_read(db, message_id)
                except Exception as e:
                    await websocket.send_json({"error": str(e)})
                    continue
                notification = {"action": "read_update", "message_id": message_id, "is_read": True}
                await manager.broadcast(chat_id, notification)
                continue

            text = message_data.get("text")
            if not text:
                await websocket.send_json({"error": "No text provided"})
                continue

            # Предотвращаем дублирование сообщений
            current_time = time.time()
            if manager.is_duplicate(chat_id, user_id, text, current_time):
                await websocket.send_json({"error": "Duplicate message detected"})
                continue

            message_service = MessageService()
            try:
                new_message = await message_service.create_message(db, chat_id, user_id, text)
            except Exception as e:
                await websocket.send_json({"error": "Error saving message: " + str(e)})
                continue

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
