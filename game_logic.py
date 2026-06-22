# backend/game_logic.py 전체 수정/업데이트본

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
# 백엔드 내부 타이머 태스크를 관리하기 위한 딕셔너리 (중복 타이머 방지)
timer_tasks = {} 

def init_room_state(room_code: str):
    if room_code not in room_states:
        room_states[room_code] = {
            "phase": GamePhase.LOBBY,
            "day_count": 0,
            "votes": {},
            "mafia_target": None,
            "doctor_target": None,
            "police_target": None,
            # [추가] 방장이 세팅할 낮/밤 기본 시간 (기본값 30초, 20초)
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

# [수정] 방장이 보낸 custom_settings 가 인자로 들어옵니다.
async def start_game(room_code: str, custom_settings: dict = None):
    users = list(manager.rooms.get(room_code, {}).keys())
    state = room_states[room_code]
    
    # 방장이 전달한 커스텀 시간 세팅값 적용
    if custom_settings:
        state["day_time"] = int(custom_settings.get("day_time", 30))
        state["night_time"] = int(custom_settings.get("night_time", 20))

    state["phase"] = GamePhase.ROLE_ASSIGN
    
    # 1인 테스트 대응 직업 분배
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

# [추가] 지정된 시간(초)이 지나면 자동으로 다음 단계 액션을 실행시키는 백그라운드 타이머 함수
async def start_phase_timer(room_code: str, duration: int, next_phase: str):
    try:
        await asyncio.sleep(duration)
        # 시간이 다 지나면 해당 페이즈로 강제 전환시킴
        if next_phase == GamePhase.VOTE:
            await change_phase(room_code, GamePhase.VOTE)
        elif next_phase == GamePhase.NIGHT:
            # 낮 투표 정산 안 끝나고 시간 종료 시 아무도 처형 안 시키고 밤으로 패스
            await tally_votes(room_code) 
        elif next_phase == GamePhase.DAY:
            # 밤 능력 정산 안 끝나고 시간 종료 시 강제 정산 후 낮으로 패스
            await check_night_settlement(room_code, force=True)
    except asyncio.CancelledError:
        pass # 중간에 투표 완료 등으로 타이머가 취소되면 안전하게 종료

async def change_phase(room_code: str, new_phase: str):
    if room_code not in room_states: return
    state = room_states[room_code]
    state["phase"] = new_phase
    
    # 기존에 돌고 있던 타이머 태스크가 있다면 인터셉트하여 취소(초기화)
    if room_code in timer_tasks and not timer_tasks[room_code].done():
        timer_tasks[room_code].cancel()
    
    if new_phase == GamePhase.DAY:
        state["day_count"] += 1
        state["votes"] = {}
        duration = state["day_time"]
        
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "DAY",
            "day": state["day_count"],
            "duration": duration,
            "message": f"☀️ {state['day_count']}일차 낮이 되었습니다. 토론 시간은 {duration}초입니다."
        })
        # 낮 토론 시간이 끝나면 자동으로 VOTE(투표) 페이즈로 넘기는 타이머 백그라운드 구동
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.VOTE))
        
    elif new_phase == GamePhase.VOTE:
        # 투표 시간은 토론 시간의 절반(최소 10초)으로 자동 할당
        duration = max(10, int(state["day_time"] / 2))
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "VOTE",
            "duration": duration,
            "message": f"🗳️ 투표 시간이 되었습니다. 제한 시간은 {duration}초입니다."
        })
        # 투표 시간이 끝나면 정산 후 밤으로 넘기는 타이머 구동
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.NIGHT))
        
    elif new_phase == GamePhase.NIGHT:
        state["mafia_target"] = None
        state["doctor_target"] = None
        state["police_target"] = None
        duration = state["night_time"]
        
        await manager.broadcast_to_room(room_code, {
            "type": "PHASE_CHANGE",
            "phase": "NIGHT",
            "duration": duration,
            "message": f"🌙 밤이 되었습니다. 능력 행사 시간은 {duration}초입니다."
        })
        # 밤 시간이 끝나면 정산 후 다음날 낮으로 넘기는 타이머 구동
        timer_tasks[room_code] = asyncio.create_task(start_phase_timer(room_code, duration, GamePhase.DAY))

async def process_vote(room_code: str, voter: str, target: str):
    state = room_states.get(room_code)
    if not state or state["phase"] != GamePhase.VOTE: return
    
    state["votes"][voter] = target
    await manager.broadcast_to_room(room_code, {
        "type": "SYSTEM",
        "message": f"누군가 투표를 마쳤습니다. ({len(state['votes'])}명 완료)"
    })
    
    # 시간 다 안 지났어도 생존자 전원이 투표 완료하면 타이머 취소하고 즉시 정산
    alive_users = [k for k, v in manager.rooms[room_code].items() if v["is_alive"]]
    if len(state["votes"]) >= len(alive_users):
        if room_code in timer_tasks:
            timer_tasks[room_code].cancel()
        await tally_votes(room_code)

async def tally_votes(room_code: str):
    state = room_states[room_code]
    if not state["votes"]:
        await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "⚖️ 투표 참여자가 없어 아무도 처형되지 않았습니다."})
    else:
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
            await manager.broadcast_to_room(room_code, {"type": "SYSTEM", "message": "⚖️ 동점 표가 발생하여 아무도 처형되지 않았습니다."})
        
    await send_user_list(room_code)
    if not check_game_over(room_code):
        await change_phase(room_code, GamePhase.NIGHT)

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

    # 전원 생존 특수직업 행동 완료 시 조기 정산
    await check_night_settlement(room_code)

# [수정] force(강제 정산) 파라미터 추가하여 시간 마감 시 자동 정산 기능 부여
async def check_night_settlement(room_code: str, force: bool = False):
    state = room_states[room_code]
    rooms_info = manager.rooms[room_code]
    
    has_mafia = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "마피아")
    has_doctor = any(u["is_alive"] for u in rooms_info.values() if u["role"] == "의사")
    
    # 강제 마감이 아니고 유저들이 아직 고민 중이면 얼리 리턴
    if not force:
        if has_mafia and not state["mafia_target"]: return
        if has_doctor and not state["doctor_target"]: return
        
    # 조건 만족 시 혹은 시간 초과(force=True) 시 기존 타이머 폭파 후 즉시 낮 전환
    if room_code in timer_tasks and not force:
        timer_tasks[room_code].cancel()
        
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
            "message": "🛡️ 지난 밤에는 의사의 활약으로 혹은 아무 일도 없어 아무도 사망하지 않았습니다."
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