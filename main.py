# backend/main.py

import os
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from connection import manager
from game_logic import process_chat, start_game, init_room_state, change_phase, GamePhase, send_user_list, process_vote, process_night_action, process_final_vote

app = FastAPI()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:5500")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5500", "http://127.0.0.1:5500"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@app.get("/api/create-room")
async def create_room_api():
    room_code = generate_room_code()
    manager.create_room(room_code)
    return {"room_code": room_code}

@app.websocket("/ws/{room_code}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, user_id: str):
    is_host = True if room_code not in manager.rooms or not manager.rooms[room_code] else False
    
    await manager.connect(websocket, room_code, user_id, is_host)
    init_room_state(room_code)
    
    await send_user_list(room_code)
    await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": f"{user_id}님이 방에 입장하셨습니다."})
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "CHAT":
                await process_chat(room_code, user_id, data.get("message"))
            elif action == "START_GAME" and manager.rooms[room_code][user_id]["is_host"]:
                custom_settings = data.get("settings") 
                await start_game(room_code, custom_settings)
            elif action == "VOTE":
                await process_vote(room_code, user_id, data.get("target"))
            elif action == "FINAL_VOTE":
                await process_final_vote(room_code, user_id, data.get("decision"))
            elif action == "NIGHT_ACTION":
                await process_night_action(room_code, user_id, data.get("target"), data.get("subAction"))
            elif action in ["RTC_OFFER", "RTC_ANSWER", "RTC_ICE"]:
                target_user = data.get("target")
                if target_user in manager.rooms[room_code]:
                    await manager.send_personal_message(room_code, target_user, {
                        "type": action, "sender": user_id, "payload": data.get("payload")
                    })
            elif action == "VOICE_STATUS":
                await manager.broadcast_to_room(room_code, {
                    "type": "USER_SPEAKING", "userId": user_id, "isSpeaking": data.get("isSpeaking")
                })
                
    except WebSocketDisconnect:
        manager.disconnect(room_code, user_id)
        await send_user_list(room_code)
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": f"{user_id}님이 퇴장하셨습니다."})