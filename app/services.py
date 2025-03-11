from app.repositories import UserRepository, ChatRepository, MessageRepository


class UserService:
    async def create_user(self, db, username: str, email: str, password: str):
        repo = UserRepository(db)
        return await repo.create_user(username, email, password)


class ChatService:
    async def create_chat(self, db, name: str, user_ids: list[int], creator_id: int):
        repo = ChatRepository(db)
        return await repo.create_chat(name, user_ids, creator_id)

    async def get_history(self, db, chat_id: int, limit: int, offset: int):
        repo = ChatRepository(db)
        return await repo.get_chat_history(chat_id, limit, offset)


class MessageService:
    async def create_message(self, db, chat_id: int, sender_id: int, text: str):
        repo = MessageRepository(db)
        return await repo.create_message(chat_id, sender_id, text)

    async def mark_read(self, db, message_id: int):
        repo = MessageRepository(db)
        return await repo.mark_message_read(message_id)
