# backend/game_logic.py
import random
import asyncio
from connection import manager

# 게임 페이즈 정의
class GamePhase:
    LOBBY = "LOBBY"
    ROLE_ASSIGN = "ROLE_ASSIGN"
    DAY = "DAY"
    VOTE = "VOTE"
    NIGHT = "NIGHT"

# 방별 게임 상태를 저장하는 딕셔너리
room_states = {}

def init_room_state(room_code: str):
    """방이 처음 생성될 때 초기 상태를 설정합니다."""
    if room_code not in room_states:
        room_states[room_code] = {
            "phase": GamePhase.LOBBY,
            "day_count": 0
        }

async def start_game(room_code: str):
    """방장이 게임 시작을 누르면 직업을 분배합니다."""
    users = list(manager.rooms.get(room_code, {}).keys())
    
    # 예시: 최소 3명부터 시작 가능
    if len(users) < 3:
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "게임 시작을 위해 최소 3명이 필요합니다."})
        return

    room_states[room_code]["phase"] = GamePhase.ROLE_ASSIGN
    
    # 직업 분배 (마피아 1명, 나머지 시민)
    # 실제 게임에서는 인원에 따라 마피아, 의사, 경찰 비율을 계산해야 합니다.
    roles = ["MAFIA"] + ["CITIZEN"] * (len(users) - 1)
    random.shuffle(roles)

    for i, user_id in enumerate(users):
        manager.rooms[room_code][user_id]["role"] = roles[i]
        manager.rooms[room_code][user_id]["is_alive"] = True
        
        # 보안 핵심: 남의 직업은 보내지 않고, '본인의 직업'만 개인 소켓으로 전송합니다.
        await manager.send_personal_message(room_code, user_id, {
            "type": "ROLE_ASSIGN",
            "role": roles[i],
            "message": f"당신의 직업은 [{roles[i]}] 입니다."
        })

    # 직업을 확인할 시간을 준 뒤 낮으로 전환
    await asyncio.sleep(3)
    await change_phase(room_code, GamePhase.DAY)

async def change_phase(room_code: str, new_phase: str):
    """게임의 낮과 밤 상태를 변경하고 클라이언트에게 알립니다."""
    if room_code not in room_states:
        return
        
    room_states[room_code]["phase"] = new_phase
    
    if new_phase == GamePhase.DAY:
        room_states[room_code]["day_count"] += 1
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "DAY",
            "day": room_states[room_code]["day_count"],
            "message": f"☀️ {room_states[room_code]['day_count']}일차 낮이 되었습니다. 토론을 시작하세요."
        })
    elif new_phase == GamePhase.NIGHT:
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "NIGHT",
            "message": "🌙 밤이 되었습니다. 시민들의 채팅과 마이크가 차단됩니다."
        })

async def process_chat(room_code: str, user_id: str, message: str):
    """서버로 들어온 채팅이 현재 페이즈와 직업에 맞는지 검증 후 전달합니다."""
    phase = room_states.get(room_code, {}).get("phase", GamePhase.LOBBY)
    user_info = manager.rooms.get(room_code, {}).get(user_id)
    
    if not user_info or not user_info["is_alive"]:
        await manager.send_personal_message(room_code, user_id, {"type": "SYSTEM", "message": "사망한 유저는 채팅할 수 없습니다."})
        return

    # 낮 시간: 누구나 전체 채팅 가능
    if phase in [GamePhase.LOBBY, GamePhase.DAY, GamePhase.VOTE]:
        await manager.broadcast_to_room(room_code, {
            "type": "CHAT",
            "sender": user_id,
            "message": message
        })
        
    # 밤 시간: 마피아 전용 채팅 모드 작동
    elif phase == GamePhase.NIGHT:
        if user_info["role"] == "MAFIA":
            # 마피아들끼리만 메시지 공유
            await manager.broadcast_mafia(room_code, {
                "type": "MAFIA_CHAT",
                "sender": user_id,
                "message": message
            })
        else:
            # 시민이 채팅을 시도하면 서버에서 차단하고 본인에게만 경고 전송
            await manager.send_personal_message(room_code, user_id, {
                "type": "SYSTEM",
                "message": "밤에는 채팅을 칠 수 없습니다."
            })