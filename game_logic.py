# backend/game_logic.py

import random
import asyncio
from connection import manager

class GamePhase:
    LOBBY = "LOBBY"
    ROLE_ASSIGN = "ROLE_ASSIGN"
    DAY = "DAY"
    VOTE = "VOTE"
    DEFENSE = "DEFENSE"       # [추가] 최후 변론
    FINAL_VOTE = "FINAL_VOTE" # [추가] 찬반 투표
    NIGHT = "NIGHT"

room_states = {}
timer_tasks = {}

def init_room_state(room_code: str):
    if room_code not in room_states:
        room_states[room_code] = {
            "phase": GamePhase.LOBBY,
            "day_count": 0,
            "votes": {},
            "final_votes": {},
            "defense_target": None,
            "mafia_target": None,
            "doctor_target": None,
            "police_target": None,
            "day_time": 30,
            "night_time": 20
        }

async def send_user_list(room_code: str):
    if room_code not in manager.rooms: return
    user_list = []
    for uid, info in manager.rooms[room_code].items():
        user_list.append({
            "userId": uid,
            "isAlive": info["is_alive"],
            "isHost": info["is_host"]
        })
    await manager.broadcast_to_room(room_code, {"type": "USER_LIST", "users": user_list})

async def start_game(room_code: str, custom_settings: dict = None):
    users = list(manager.rooms.get(room_code, {}).keys())
    state = room_states[room_code]
    
    if custom_settings:
        state["day_time"] = int(custom_settings.get("day_time", 30))
        state["night_time"] = int(custom_settings.get("night_time", 20))

    state["phase"] = GamePhase.ROLE_ASSIGN
    
    base_roles = ["마피아", "의사", "경찰"]
    if len(users) <= 3:
        roles = base_roles[:len(users)]
    else:
        roles = base_roles + ["시민"] * (len(users) - 3)
        
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
    await asyncio.sleep(2)
    await change_phase(room_code, GamePhase.DAY)

async def start_phase_timer(room_code: str, duration: int, next_phase: str):
    try:
        await asyncio.sleep(duration)
        state = room_states.get(room_code)
        if not state: return

        if next_phase == GamePhase.VOTE:
            await change_phase(room_code, GamePhase.VOTE)
        elif next_phase == GamePhase.DEFENSE:
            await tally_votes(room_code)
        elif next_phase == GamePhase.FINAL_VOTE:
            await change_phase(room_code, GamePhase.FINAL_VOTE)
        elif next_phase == GamePhase.NIGHT:
            if state["phase"] == GamePhase.FINAL_VOTE:
                await tally_final_votes(room_code)
            elif state["phase"] == GamePhase.VOTE:
                await tally_votes(room_code)
            else:
                await change_phase(room_code, GamePhase.NIGHT)
        elif next_phase == GamePhase.DAY:
            await check_night_settlement(room_code, force=True)
    except asyncio.CancelledError:
        pass

async def change_phase(room_code: str, new_phase: str):
    if room_code not in room_states: return
    state = room_states[room_code]
    state["phase"] = new_phase
    
    if room_code in timer_tasks and not timer_tasks[room_code].done():
        timer_tasks[room_code].cancel()
    
    if new_phase == GamePhase.DAY:
        state["day_count"] += 1
        state["votes"] = {}
        state["final_votes"] = {}
        state["defense_target"] = None
        duration = state["day_time"]
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE", "phase": "DAY", "day": state["day_count"], "duration": duration,
            "message": f"☀️ {state['day_count']}일차 낮이 되었습니다. 토론 시간은 {duration}초입니다."
        })
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.VOTE))
        
    elif new_phase == GamePhase.VOTE:
        duration = max(10, int(state["day_time"] / 2))
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE", "phase": "VOTE", "duration": duration,
            "message": f"🗳️ 투표 시간이 되었습니다. 의심되는 사람을 투표하세요. ({duration}초)"
        })
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.NIGHT))
        
    elif new_phase == GamePhase.DEFENSE:
        duration = 15
        target = state["defense_target"]
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE", "phase": "DEFENSE", "duration": duration, "target": target,
            "message": f"⚖️ 최다 득표자 [{target}]님의 최후 변론 시간입니다. 채팅으로 변론하세요! ({duration}초)"
        })
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.FINAL_VOTE))

    elif new_phase == GamePhase.FINAL_VOTE:
        duration = 10
        target = state["defense_target"]
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE", "phase": "FINAL_VOTE", "duration": duration, "target": target,
            "message": f"🗳️ [{target}]님을 처형하시겠습니까? 우측 명단에서 찬성/반대를 투표하세요. ({duration}초)"
        })
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.NIGHT))

    elif new_phase == GamePhase.NIGHT:
        state["mafia_target"] = None
        state["doctor_target"] = None
        state["police_target"] = None
        duration = state["night_time"]
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE", "phase": "NIGHT", "duration": duration,
            "message": f"🌙 밤이 되었습니다. 능력 행사 시간은 {duration}초입니다."
        })
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.DAY))

async def process_vote(room_code: str, voter: str, target: str):
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.VOTE: return
    
    state["votes"][voter] = target
    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM", "message": f"누군가 지목 투표를 마쳤습니다. ({len(state['votes'])}명 완료)"
    })
    
    alive_users = [k for k, v in manager.rooms[room_code].items() if v["is_alive"]]
    if len(state["votes"]) >= len(alive_users):
        if room_code in timer_tasks: timer_tasks[room_code].cancel()
        await tally_votes(room_code)

async def process_final_vote(room_code: str, voter: str, decision: str):
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.FINAL_VOTE: return
    
    state["final_votes"][voter] = decision
    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM", "message": f"누군가 찬반 투표를 마쳤습니다. ({len(state['final_votes'])}명 완료)"
    })
    
    alive_users = [k for k, v in manager.rooms[room_code].items() if v["is_alive"] and k != state["defense_target"]]
    if len(state["final_votes"]) >= len(alive_users):
        if room_code in timer_tasks: timer_tasks[room_code].cancel()
        await tally_final_votes(room_code)

async def tally_votes(room_code: str):
    state = room_states[room_code]
    if not state["votes"]:
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "⚖️ 투표 참여자가 없어 아무도 처형되지 않았습니다."})
        if not check_game_over(room_code): await change_phase(room_code, GamePhase.NIGHT)
        return

    vote_counts = {}
    for target in state["votes"].values():
        vote_counts[target] = vote_counts.get(target, 0) + 1
        
    max_votes = max(vote_counts.values())
    candidates = [k for k, v in vote_counts.items() if v == max_votes]
    
    if len(candidates) == 1:
        state["defense_target"] = candidates[0]
        await change_phase(room_code, GamePhase.DEFENSE)
    else:
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "⚖️ 동점 표가 발생하여 아무도 처형되지 않았습니다."})
        if not check_game_over(room_code): await change_phase(room_code, GamePhase.NIGHT)

async def tally_final_votes(room_code: str):
    state = room_states[room_code]
    target = state["defense_target"]
    yes_votes = list(state["final_votes"].values()).count("YES")
    no_votes = list(state["final_votes"].values()).count("NO")

    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM", "message": f"⚖️ 찬반 투표 결과 - 찬성: {yes_votes} / 반대: {no_votes}"
    })

    if yes_votes > no_votes:
        manager.rooms[room_code][target]["is_alive"] = False
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": f"💀 과반수 찬성으로 [{target}]님이 처형되었습니다."})
    else:
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": f"🛡️ 찬성이 과반을 넘지 않아 [{target}]님이 생존했습니다."})

    await send_user_list(room_code)
    if not check_game_over(room_code): await change_phase(room_code, GamePhase.NIGHT)

async def process_night_action(room_code: str, actor: str, target: str, action: str):
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.NIGHT: return
    
    role = manager.rooms[room_code][actor]["role"]
    
    if action == "KILL" and role == "마피아":
        state["mafia_target"] = target
    elif action == "HEAL" and role == "의사":
        state["doctor_target"] = target
    elif action == "INVESTIGATE" and role == "경찰":
        target_role = manager.rooms[room_code][target]["role"]
        await manager.send_personal_message(room_code, actor, {"type": "SYSTEM", "message": f"🔍 조사 결과: [{target}]님은 **{target_role}**입니다."})
        state["police_target"] = target

    await check_night_settlement(room_code)

async def check_night_settlement(room_code: str, force: bool = False):
    state = room_states[room_code]
    rooms_info = manager.rooms[room_code]
    
    has_mafia = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "마피아")
    has_doctor = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "의사")
    
    if not force:
        if has_mafia and not state["mafia_target"]: return
        if has_doctor and not state["doctor_target"]: return
        
    if room_code in timer_tasks and not force:
        timer_tasks[room_code].cancel()
        
    kill_target = state["mafia_target"]
    heal_target = state["doctor_target"]
    
    # [수정] 의사 방어 성공 메시지 추가
    if kill_target and kill_target == heal_target:
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": "🛡️ 의사가 치료를 성공해서 시민을 살렸습니다."
        })
    elif kill_target:
        rooms_info[kill_target]["is_alive"] = False
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": f"🩸 지난 밤, 마피아에게 [{kill_target}]님이 피습당해 사망하셨습니다."
        })
    else:
        await manager.broadcast_to_room(room_code, {
            "type": "SYSTEM",
            "message": "🌙 지난 밤에는 아무 일도 일어나지 않았습니다."
        })
        
    await send_user_list(room_code)
    if not check_game_over(room_code):
        await change_phase(room_code, GamePhase.DAY)

def check_game_over(room_code: str) -> bool:
    rooms_info = manager.rooms[room_code]
    mafias = len([k for k, v in rooms_info.items() if v["role"] == "마피아" and v["is_alive"]])
    citizens = len([k for k, v in rooms_info.items() if v["role"] != "마피아" and v["is_alive"]])
    
    # [수정] 1인 테스트 시 강제 종료 및 게임 멈춤 방지
    if len(rooms_info) <= 1:
        return False 

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

    if phase in [GamePhase.LOBBY, GamePhase.DAY, GamePhase.VOTE, GamePhase.DEFENSE, GamePhase.FINAL_VOTE]:
        await manager.broadcast_to_room(room_code, {"type": "CHAT", "sender": user_id, "message": message})
    elif phase == GamePhase.NIGHT and user_info["role"] == "마피아":
        await manager.broadcast_to_mafia(room_code, {"type": "MAFIA_CHAT", "sender": user_id, "message": message})