# backend/game_logic.py
import random
import asyncio
from connection import manager

class GamePhase:
    LOBBY = "LOBBY"
    ROLE_ASSIGN = "ROLE_ASSIGN"
    DAY = "DAY"
    VOTE = "VOTE"
    NIGHT = "NIGHT"

room_states = {}

def init_room_state(room_code: str):
    if room_code not in room_states:
        room_states[room_code] = {
            "phase": GamePhase.LOBBY,
            "day_count": 0,
            "votes": {},          # {투표자: 피투표자}
            "mafia_target": None, # 마피아가 지목한 대상
            "doctor_target": None,# 의사가 지목한 대상
            "police_target": None # 경찰이 지목한 대상
        }

async def send_user_list(room_code: str):
    """현재 방의 유저 목록과 생존 여부, 방장 상태를 브로드캐스트 (직업은 은폐)"""
    if room_code not in manager.rooms: return
    user_list = []
    for uid, info in manager.rooms[room_code].items():
        user_list.append({
            "userId": uid,
            "isAlive": info["is_alive"],
            "isHost": info["is_host"]
        })
    await manager.broadcast_to_room(room_code, {
        "type": "USER_LIST",
        "users": user_list
    })

async def start_game(room_code: str):
    users = list(manager.rooms.get(room_code, {}).keys())
    if len(users) < 4: # 마피아, 시민, 의사, 경찰 구성을 위해 최소 4명 추천
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "게임 시작을 위해 최소 4명이 필요합니다."})
        return

    room_states[room_code]["phase"] = GamePhase.ROLE_ASSIGN
    
    # 직업 분배 (한글화 요구사항 반영)
    roles = ["마피아", "의사", "경찰"] + ["시민"] * (len(users) - 3)
    random.shuffle(roles)

    for i, user_id in enumerate(users):
        manager.rooms[room_code][user_id]["role"] = roles[i]
        manager.rooms[room_code][user_id]["is_alive"] = True
        
        await manager.send_personal_message(room_code, user_id, {
            "type": "ROLE_ASSIGN",
            "role": roles[i],
            "isHost": manager.rooms[room_code][user_id]["is_host"],
            "message": f"당신의 직업은 [{roles[i]}] 입니다."
        })

    await send_user_list(room_code)
    await asyncio.sleep(4)
    await change_phase(room_code, GamePhase.DAY)

async def change_phase(room_code: str, new_phase: str):
    if room_code not in room_states: return
    
    state = room_states[room_code]
    state["phase"] = new_phase
    
    if new_phase == GamePhase.DAY:
        state["day_count"] += 1
        state["votes"] = {}
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "DAY",
            "day": state["day_count"],
            "message": f"☀️ {state['day_count']}일차 낮이 되었습니다. 자유롭게 대화하세요."
        })
        
    elif new_phase == GamePhase.VOTE:
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "VOTE",
            "message": "🗳️ 투표 시간이 되었습니다. 의심스러운 인물을 우측 목록에서 투표하세요."
        })
        
    elif new_phase == GamePhase.NIGHT:
        state["mafia_target"] = None
        state["doctor_target"] = None
        state["police_target"] = None
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "NIGHT",
            "message": "🌙 밤이 되었습니다. 마이크가 차단되며 특수 직업은 행동을 개시합니다."
        })

async def process_vote(room_code: str, voter: str, target: str):
    """낮 투표 처리"""
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.VOTE: return
    
    state["votes"][voter] = target
    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM",
        "message": f" 누군가 투표를 마쳤습니다. ({len(state['votes'])}명 완료)"
    })
    
    # 생존자 전원이 투표 완료하면 정산
    alive_users = [k for k, v in manager.rooms[room_code].items() if v["is_alive"]]
    if len(state["votes"]) >= len(alive_users):
        await tally_votes(room_code)

async def tally_votes(room_code: str):
    state = room_states[room_code]
    if not state["votes"]: return
    
    # 최다 득표자 계산
    vote_counts = {}
    for target in state["votes"].values():
        vote_counts[target] = vote_counts.get(target, 0) + 1
        
    max_votes = max(vote_counts.values())
    candidates = [k for k, v in vote_counts.items() if v == max_votes]
    
    if len(candidates) == 1:
        executed = candidates[0]
        manager.rooms[room_code][executed]["is_alive"] = False
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": f"💀 투표 결과, 최다 득표자 [{executed}]님이 처형되었습니다."
        })
    else:
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": "⚖️ 동점 표가 발생하여 아무도 처형되지 않았습니다."
        })
        
    await send_user_list(room_code)
    if not check_game_over(room_code):
        await change_phase(room_code, GamePhase.NIGHT)

async def process_night_action(room_code: str, actor: str, target: str, action: str):
    """밤의 특수 능력 처리"""
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.NIGHT: return
    
    role = manager.rooms[room_code][actor]["role"]
    
    if action == "KILL" and role == "마피아":
        state["mafia_target"] = target
        await manager.send_personal_message(room_code, actor, {"type": "SYSTEM", "message": f"[{target}]님을 저격 대상으로 지목했습니다."})
    elif action == "HEAL" and role == "의사":
        state["doctor_target"] = target
        await manager.send_personal_message(room_code, actor, {"type": "SYSTEM", "message": f"[{target}]님을 치료 대상으로 지목했습니다."})
    elif action == "INVESTIGATE" and role == "경찰":
        target_role = manager.rooms[room_code][target]["role"]
        # 경찰에게만 은밀히 조사 결과 전송
        await manager.send_personal_message(room_code, actor, {
            "type": "SYSTEM", 
            "message": f"🔍 조사 결과: [{target}]님은 **{target_role}**입니다."
        })
        state["police_target"] = target

    # 밤 정산 트리거 조건 (모든 생존 특수직업이 행동을 완료했을 때)
    await check_night_settlement(room_code)

async def check_night_settlement(room_code: str):
    state = room_states[room_code]
    rooms_info = manager.rooms[room_code]
    
    # 생존해 있는 특수직업 확인
    has_mafia = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "마피아")
    has_doctor = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "의사")
    
    # 행동 완료 검증
    if has_mafia and not state["mafia_target"]: return
    if has_doctor and not state["doctor_target"]: return
    
    # 밤 정산 결과 도출
    kill_target = state["mafia_target"]
    heal_target = state["doctor_target"]
    
    if kill_target and kill_target != heal_target:
        rooms_info[kill_target]["is_alive"] = False
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": f"🩸 지난 밤, 마피아에게 [{kill_target}]님이 피습당해 사망하셨습니다."
        })
    else:
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": "🛡️ 지난 밤에는 의사의 활약으로 아무도 사망하지 않았습니다."
        })
        
    await send_user_list(room_code)
    if not check_game_over(room_code):
        await change_phase(room_code, GamePhase.DAY)

def check_game_over(room_code: str) -> bool:
    rooms_info = manager.rooms[room_code]
    mafias = len([k for k, v in rooms_info.items() if v["role"] == "마피아" and v["is_alive"]])
    citizens = len([k for k, v in rooms_info.items() if v["role"] != "마피아" and v["is_alive"]])
    
    if mafias == 0:
        manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "🎉 마피아가 모두 소탕되었습니다! 시민 팀의 승리입니다."})
        return True
    if mafias >= citizens:
        manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "🩸 마피아의 수가 시민과 같거나 많아졌습니다! 마피아 팀의 승리입니다."})
        return True
    return False

async def process_chat(room_code: str, user_id: str, message: str):
    phase = room_states.get(room_code, {}).get("phase", GamePhase.LOBBY)
    user_info = manager.rooms.get(room_code, {}).get(user_id)
    
    if not user_info or not user_info["is_alive"]: return

    if phase in [GamePhase.LOBBY, GamePhase.DAY, GamePhase.VOTE]:
        await manager.broadcast_to_room(room_code, {"type": "CHAT", "sender": user_id, "message": message})
    elif phase == GamePhase.NIGHT and user_info["role"] == "마피아":
        await manager.broadcast_to_mafia(room_code, {"type": "MAFIA_CHAT", "sender": user_id, "message": message})