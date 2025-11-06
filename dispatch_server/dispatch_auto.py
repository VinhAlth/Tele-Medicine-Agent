import os
import asyncio
import time
import json
from dotenv import load_dotenv
from livekit import api

# optional redis (synchronous client). We'll call it in a thread to avoid blocking the event loop.
try:
    import redis
except Exception:
    redis = None

load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# ------ Redis connection defaults (override with env if you prefer) ------
REDIS_HOST = os.getenv("REDIS_HOST", "redis-connect.dev.longvan.vn")
REDIS_PORT = int(os.getenv("REDIS_PORT", "32276"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "111111aA")
REDIS_HASH_KEY = os.getenv("REDIS_HASH_KEY", "room:online")
# -----------------------------------------------------------------------

# Danh sách phòng cho từng loại agent (static)
MEDICAL_ROOMS = {f"PhongDangKy{i:02}" for i in range(1, 3)}
ASSISTANT_ROOMS = {f"PhongKham{i:02}" for i in range(1, 3)}
RECORD_ROOMS = {f"PhongHop{i:02}" for i in range(1, 2)}
TEST_ROOMS = {f"Test{i:02}" for i in range(1, 3)}

# ROOMS_TO_MONITOR ban đầu (static); sau này khi chạy sẽ kết hợp với redis_rooms động
STATIC_ROOMS_TO_MONITOR = TEST_ROOMS | ASSISTANT_ROOMS

# Ghi nhớ các phòng đã được dispatch
dispatched_rooms = set()

def now():
    return time.strftime("[%H:%M:%S]")

# --- Redis helper (synchronous) ---
def fetch_redis_room_names_sync():
    """
    Kết nối Redis (synchronous) và trả về set các roomName từ hash REDIS_HASH_KEY.
    Nếu redis package không có hoặc lỗi kết nối -> trả về empty set.
    """
    rooms = set()
    if redis is None:
        print(f"{now()} ⚠️ 'redis' package không khả dụng, bỏ qua redis rooms.")
        return rooms

    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        all_fields = r.hgetall(REDIS_HASH_KEY)  # trả về dict {field: value_json}
        for field, value_json in all_fields.items():
            try:
                data = json.loads(value_json)
                room_name = data.get("roomName")
                if room_name:
                    rooms.add(room_name)
            except json.JSONDecodeError:
                print(f"{now()} ⚠️ Lỗi decode JSON cho field {field}")
    except Exception as e:
        print(f"{now()} ⚠️ Không thể kết nối Redis hoặc đọc key {REDIS_HASH_KEY}: {repr(e)}")
    return rooms

# Async wrapper to call sync Redis fetch in a thread to avoid blocking event loop
async def fetch_redis_room_names():
    return await asyncio.to_thread(fetch_redis_room_names_sync)

# --- Dispatch agent ---
async def dispatch_agent(lkapi, room_name: str, agent_name: str):
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
        )
        print(f"{now()} ✅ Dispatched {agent_name} to {room_name}")
        dispatched_rooms.add(room_name)
    except Exception as e:
        print(f"{now()} ❌ Error dispatching {agent_name} to {room_name}: {repr(e)}")

# --- Safe wrappers ---
async def safe_list_rooms(lkapi):
    try:
        resp = await lkapi.room.list_rooms(api.ListRoomsRequest())
        return resp
    except Exception as e:
        print(f"{now()} ⚠️ list_rooms failed: {repr(e)}")
        return None

async def safe_list_participants(lkapi, room_name):
    try:
        resp = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return resp
    except Exception as e:
        print(f"{now()} ⚠️ list_participants failed for room={room_name}: {repr(e)}")
        return None

async def safe_remove_participant(lkapi, room_name, identity, label):
    try:
        # note: keep same call shape as original code
        await lkapi.room.remove_participant(api.RoomParticipantIdentity(room=room_name, identity=identity))
        print(f"{now()} ❌ Removed {label} (identity={identity}) khỏi {room_name}")
        return True
    except Exception as e:
        print(f"{now()} ❌ Failed to remove {label} (identity={identity}) in {room_name}: {repr(e)}")
        return False

# --- Kiểm tra các room thuộc "rooms_to_monitor" (cả static + dynamic từ redis) ---
async def disconnect_specific_agents_in_tests(lkapi, rooms_to_monitor):
    resp = await safe_list_rooms(lkapi)
    if resp is None:
        return

    for room_info in resp.rooms:
        room_name = getattr(room_info, "name", "")
        # chỉ quan tâm các room trong rooms_to_monitor (đã bao gồm redis rooms động)
        if room_name not in rooms_to_monitor:
            continue

        num = getattr(room_info, "num_participants", 0)
        print(f"{now()} 👥 Room {room_name} có {num} người (tổng, bao gồm agent nếu có).")

        participants_resp = await safe_list_participants(lkapi, room_name)
        if participants_resp is None:
            continue

        # Log danh sách participants
        print(f"{now()} ℹ️ Participants in {room_name}:")
        for p in participants_resp.participants:
            print(f"    - identity='{p.identity}' name='{p.name}'")

        # --- Logic mới: nếu có cả ingress_agent và assistant_agent, remove assistant_agent sau 20s ---
        ingress_exists = any((p.identity or "").strip() == "ingress_agent" for p in participants_resp.participants)
        assistant_exists = any((p.identity or "").strip() == "assistant_agent" for p in participants_resp.participants)

        if ingress_exists and assistant_exists:
            async def delayed_remove():
                await asyncio.sleep(5)
                for p in participants_resp.participants:
                    pid = (p.identity or "").strip()
                    pname = (p.name or "").strip()
                    if pid == "assistant_agent":
                        await safe_remove_participant(lkapi, room_name, pid, pname)
                        print(f"{now()} ⏱  Removed assistant_agent after 20s in {room_name}")

            asyncio.create_task(delayed_remove())

        # --- Trường hợp tổng > 3 ---
        if num >= 3:
            kicked_count = 0
            for p in participants_resp.participants:
                pid = (p.identity or "").strip()
                pname = (p.name or "").strip()

                # Remove assistant_agent nếu đúng identity
                if pid == "assistant_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                        # Sau khi remove assistant_agent thì dispatch record_agent
                        # await dispatch_agent(lkapi, room_name, "record_agent")

                # Sau khi remove ingress_agent thì dispatch record_agent
                elif pid == "ingress_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                        await dispatch_agent(lkapi, room_name, "record_agent")

            if kicked_count == 0:
                print(f"{now()} ℹ️ Không tìm thấy participant mục tiêu để remove trong {room_name}.")
            else:
                print(f"{now()} ℹ️ Đã remove {kicked_count} participant(s) trong {room_name}.")

        # --- Trường hợp chỉ còn 1 người là ingress_agent ---
        elif num == 1:
            if not participants_resp.participants:
                print(f"{now()} ⚠️ Room {room_name} có num=1 nhưng participants list rỗng -> skip")
                continue

            p = participants_resp.participants[0]
            pid = (p.identity or "").strip()
            pname = (p.name or "").strip()
            if pid == "ingress_agent":
                await safe_remove_participant(lkapi, room_name, pid, pname)
                print(f"{now()} ℹ️ Room {room_name} chỉ còn 1 người và là ingress_agent -> removed")


# --- Main loop ---
async def monitor_and_dispatch():
    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    try:
        while True:
            # 1) Lấy dynamic redis rooms (được gọi mỗi vòng) -- không chặn event loop
            try:
                redis_rooms = await fetch_redis_room_names()
            except Exception as e:
                print(f"{now()} ⚠️ Lỗi khi fetch redis rooms: {repr(e)}")
                redis_rooms = set()

            # 2) Compose rooms_to_monitor dynamic (static + redis)
            rooms_to_monitor = set(STATIC_ROOMS_TO_MONITOR) | set(redis_rooms)

            # 3) List all rooms từ livekit
            resp = await safe_list_rooms(lkapi)
            if resp is None:
                await asyncio.sleep(1)
                continue

            active_rooms = set()

            for room in resp.rooms:
                room_name = getattr(room, "name", "")
                active_rooms.add(room_name)
                num_participants = getattr(room, "num_participants", 0)

                # Khi phát hiện room có participant và chưa dispatch -> dispatch tương ứng
                if num_participants > 0 and room_name not in dispatched_rooms:
                    # Nếu chỉ có 1 người thì check người đó không phải ingress_agent mới dispatch
                    if num_participants == 1:
                        participants_resp = await safe_list_participants(lkapi, room_name)
                        if not participants_resp or not participants_resp.participants:
                            continue
                        only_pid = (participants_resp.participants[0].identity or "").strip()
                        if only_pid == "ingress_agent":
                            print(f"{now()} ⚠️ Room {room_name} chỉ có ingress_agent -> không dispatch.")
                            
                            continue

                    agent_name = None
                    if room_name in MEDICAL_ROOMS:
                        agent_name = "medical_agent"
                    elif room_name in ASSISTANT_ROOMS:
                        agent_name = "assistant_agent"
                    elif room_name in RECORD_ROOMS:
                        agent_name = "record_agent"
                    elif room_name in TEST_ROOMS:
                        agent_name = "test_agent"
                    elif room_name in redis_rooms:
                        agent_name = "assistant_agent"

                    if agent_name:
                        await dispatch_agent(lkapi, room_name, agent_name)


            # Clean up dispatched_rooms (nếu room không tồn tại nữa hoặc không có participants -> remove khỏi dispatched_rooms)
            for room_name in list(dispatched_rooms):
                room_obj = next((r for r in resp.rooms if getattr(r, "name", None) == room_name), None)
                if not room_obj or getattr(room_obj, "num_participants", 0) == 0:
                    print(f"{now()} 🧹 Room {room_name} is empty -> reset dispatch")
                    dispatched_rooms.remove(room_name)

            # --- Áp dụng logic remove cho tất cả rooms_to_monitor (bao gồm redis dynamic) ---
            await disconnect_specific_agents_in_tests(lkapi, rooms_to_monitor)


            interval = 1 
            await asyncio.sleep(interval)

    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(monitor_and_dispatch())
