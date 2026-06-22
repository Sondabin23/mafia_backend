# backend/connection.py
from fastapi import WebSocket
from typing import Dict, Any

class ConnectionManager:
    def __init__(self):
        # 데이터 구조: { "방코드": { "유저ID": {"ws": WebSocket, "role": str, "is_alive": bool, "is_host": bool} } }
        self.rooms: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def create_room(self, room_code: str):
        """새로운 방을 생성합니다."""
        if room_code not in self.rooms:
            self.rooms[room_code] = {}

    async def connect(self, websocket: WebSocket, room_code: str, user_id: str, is_host: bool = False):
        """유저가 소켓에 연결될 때 방에 추가합니다."""
        await websocket.accept()
        
        if room_code not in self.rooms:
            self.create_room(room_code)
            
        # 초기 상태 저장 (직업은 게임 시작 시 할당)
        self.rooms[room_code][user_id] = {
            "ws": websocket,
            "role": "CITIZEN", # 기본값, 추후 game_logic에서 덮어씀
            "is_alive": True,
            "is_host": is_host
        }

    def disconnect(self, room_code: str, user_id: str):
        """유저 연결 해제 시 방에서 제거합니다."""
        if room_code in self.rooms and user_id in self.rooms[room_code]:
            del self.rooms[room_code][user_id]
            # 방에 남은 사람이 없으면 방 폭파
            if not self.rooms[room_code]:
                del self.rooms[room_code]

    async def broadcast_to_room(self, room_code: str, message: dict):
        """방에 있는 모든 유저에게 메시지를 전송합니다."""
        if room_code in self.rooms:
            for user in self.rooms[room_code].values():
                await user["ws"].send_json(message)

    async def broadcast_to_mafia(self, room_code: str, message: dict):
        """방에 있는 생존한 '마피아' 직업 유저에게만 메시지를 은밀하게 전송합니다."""
        if room_code in self.rooms:
            for user in self.rooms[room_code].values():
                if user["role"] == "MAFIA" and user["is_alive"]:
                    await user["ws"].send_json(message)
                    
    async def send_personal_message(self, room_code: str, user_id: str, message: dict):
        """특정 유저 1명에게만 메시지를 전송합니다 (직업 안내 등 보안 데이터용)."""
        if room_code in self.rooms and user_id in self.rooms[room_code]:
            await self.rooms[room_code][user_id]["ws"].send_json(message)

# 싱글톤 패턴으로 앱 전체에서 하나의 manager 인스턴스 공유
manager = ConnectionManager()