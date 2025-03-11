from fastapi import WebSocket
from typing import List, Dict


class ConnectionManager:
    def __init__(self):
        # chat_id -> список WebSocket-соединений
        self.active_connections: Dict[int, List[WebSocket]] = {}
        self.last_message_timestamps: Dict[tuple, float] = {}  # (chat_id, sender_id, text) -> время отправки

    async def connect(self, chat_id: int, websocket: WebSocket):
        await websocket.accept()
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = []
        self.active_connections[chat_id].append(websocket)

    def disconnect(self, chat_id: int, websocket: WebSocket):
        if chat_id in self.active_connections:
            self.active_connections[chat_id].remove(websocket)

    async def broadcast(self, chat_id: int, message: dict):
        if chat_id in self.active_connections:
            for connection in self.active_connections[chat_id]:
                await connection.send_json(message)

    def is_duplicate(self, chat_id: int, sender_id: int, text: str, current_time: float, threshold: float = 1.0):
        key = (chat_id, sender_id, text)
        last_time = self.last_message_timestamps.get(key, 0)
        if current_time - last_time < threshold:
            return True
        self.last_message_timestamps[key] = current_time
        return False

manager = ConnectionManager()
