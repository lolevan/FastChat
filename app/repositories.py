from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models import User, Chat, Message


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_user(self, username: str, email: str, password: str) -> User:
        user = User(username=username, email=email, password=password)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def get_user_by_username(self, username: str) -> User:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

class ChatRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_chat(self, name: str, user_ids: list[int], creator_id: int) -> Chat:
        chat = Chat(name=name, type="group", creator_id=creator_id)
        result = await self.db.execute(select(User).where(User.id.in_(user_ids)))
        users = result.scalars().all()
        if not users:
            raise Exception("Users not found")
        chat.users = users
        self.db.add(chat)
        await self.db.commit()
        await self.db.refresh(chat)
        return chat

    async def get_chat_history(self, chat_id: int, limit: int, offset: int) -> list[Message]:
        result = await self.db.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.asc()).offset(offset).limit(limit)
        )
        return result.scalars().all()


class MessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_message(self, chat_id: int, sender_id: int, text: str) -> Message:
        message = Message(chat_id=chat_id, sender_id=sender_id, text=text)
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def mark_message_read(self, message_id: int) -> Message:
        result = await self.db.execute(select(Message).where(Message.id == message_id))
        message = result.scalar_one_or_none()
        if not message:
            raise Exception("Message not found")
        message.is_read = True
        await self.db.commit()
        return message
