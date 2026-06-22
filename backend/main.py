# backend/main.py
import os
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# 분리된 로직 모듈 임포트
from connection import manager
from game_logic import process_chat, start_game, init_room_state, change_phase, GamePhase

app = FastAPI()

# 환경 변수에서 프론트엔드 주소를 가져옴 (배포 시 Render 대시보드에서 설정)
# 로컬 테스트를 위해 기본값(localhost)도 포함합니다.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:5500")

app.add_middleware(
    CORSMiddleware,
    # Vercel 도메인과 로컬호스트를 모두 허용
    allow_origins=[FRONTEND_URL, "http://localhost:5500", "http://127.0.0.1:5500"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def generate_room_code(length=6):
    """6자리 영문 대문자+숫자 고유 방 코드 생성"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@app.get("/api/create-room")
async def create_room_api():
    """방 생성 API"""
    room_code = generate_room_code()
    manager.create_room(room_code)
    return {"room_code": room_code}

@app.websocket("/ws/{room_code}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, user_id: str):
    """웹소켓 통신 엔드포인트"""
    # 첫 입장 유저에게 방장 권한 부여
    is_host = True if room_code not in manager.rooms or not manager.rooms[room_code] else False
    
    await manager.connect(websocket, room_code, user_id, is_host)
    init_room_state(room_code)
    
    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM",
        "message": f"{user_id}님이 방에 입장하셨습니다."
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "CHAT":
                await process_chat(room_code, user_id, data.get("message"))
                
            elif action == "START_GAME":
                if manager.rooms[room_code][user_id]["is_host"]:
                    await start_game(room_code)
                    
            elif action == "TEST_NIGHT_TOGGLE":
                if manager.rooms[room_code][user_id]["is_host"]:
                    await change_phase(room_code, GamePhase.NIGHT)
                    
            elif action == "TEST_DAY_TOGGLE":
                if manager.rooms[room_code][user_id]["is_host"]:
                    await change_phase(room_code, GamePhase.DAY)
                
    except WebSocketDisconnect:
        manager.disconnect(room_code, user_id)
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": f"{user_id}님이 퇴장하셨습니다."
        })