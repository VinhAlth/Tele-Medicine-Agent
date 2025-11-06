import os
import asyncio
import time
from dotenv import load_dotenv
from livekit import api

load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Danh sách phòng cho từng loại agent
MEDICAL_ROOMS = {f"PhongDangKy{i:02}" for i in range(1, 11)}
ASSISTANT_ROOMS = {f"PhongKham{i:02}" for i in range(1, 11)}
RECORD_ROOMS = {f"PhongHop{i:02}" for i in range(1, 11)}
TEST_ROOMS = {f"Test{i:02}" for i in range(1, 11)}
# Nhóm phòng để apply logic remove agent
ROOMS_TO_MONITOR = TEST_ROOMS | ASSISTANT_ROOMS
# Ghi nhớ các phòng đã được dispatch
dispatched_rooms = set()

def now():
    return time.strftime("[%H:%M:%S]")

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
        await lkapi.room.remove_participant(api.RoomParticipantIdentity(room=room_name, identity=identity))
        print(f"{now()} ❌ Removed {label} (identity={identity}) khỏi {room_name}")
        return True
    except Exception as e:
        print(f"{now()} ❌ Failed to remove {label} (identity={identity}) in {room_name}: {repr(e)}")
        return False

# --- Kiểm tra TEST_ROOMS ---
async def disconnect_specific_agents_in_tests(lkapi):
    resp = await safe_list_rooms(lkapi)
    if resp is None:
        return

    for room_info in resp.rooms:
        room_name = getattr(room_info, "name", "")
        if room_name not in ROOMS_TO_MONITOR:
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

        # --- Trường hợp tổng > 3 ---
        if num >= 3:
            kicked_count = 0
            for p in participants_resp.participants:
                pid = (p.identity or "").strip()
                pname = (p.name or "").strip()

                # Remove ingress_agent nếu đúng name
                if pid == "ingress_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                # Remove assistant_age"""  """nt
                elif pid == "assistant_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                        # Sau khi remove assistant_agent thì dispatch record_agent
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
            resp = await safe_list_rooms(lkapi)
            if resp is None:
                await asyncio.sleep(5)
                continue

            active_rooms = set()

            for room in resp.rooms:
                room_name = getattr(room, "name", "")
                active_rooms.add(room_name)
                num_participants = getattr(room, "num_participants", 0)

                if num_participants > 0 and room_name not in dispatched_rooms:
                    agent_name = None
                    if room_name in MEDICAL_ROOMS:
                        agent_name = "medical_agent"
                    elif room_name in ASSISTANT_ROOMS:
                        agent_name = "assistant_agent"
                    elif room_name in RECORD_ROOMS:
                        agent_name = "record_agent"
                    elif room_name in TEST_ROOMS:
                        agent_name = "test_agent"

                    if agent_name:
                        await dispatch_agent(lkapi, room_name, agent_name)

            # Clean up dispatched_rooms
            for room_name in list(dispatched_rooms):
                room_obj = next((r for r in resp.rooms if getattr(r, "name", None) == room_name), None)
                if not room_obj or getattr(room_obj, "num_participants", 0) == 0:
                    print(f"{now()} 🧹 Room {room_name} is empty -> reset dispatch")
                    dispatched_rooms.remove(room_name)

            # Kiểm tra TEST_ROOMS
            await disconnect_specific_agents_in_tests(lkapi)

            await asyncio.sleep(5)

    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(monitor_and_dispatch())
