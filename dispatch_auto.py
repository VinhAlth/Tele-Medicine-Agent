import os
import asyncio
import time
from dotenv import load_dotenv
from livekit import api

load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Danh sách phòng được phép
PHONGKHAM_ROOMS = {f"PhongKham{i:02}" for i in range(1, 11)}
LETAN_ROOMS = {f"LeTan{i:02}" for i in range(1, 11)}

# Theo dõi phòng đang xử lý để tránh dispatch lặp
dispatched_rooms = {}  # room_name -> timestamp của lần dispatch cuối

async def dispatch_agent(room_name: str, agent_name: str):
    lkapi = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
        )
        print(f"Dispatched {agent_name} to {room_name}")
        dispatched_rooms[room_name] = time.time()  # Lưu thời gian dispatch
    except Exception as e:
        print(f"Error dispatching to {room_name}: {e}")
    await lkapi.aclose()

async def has_agent(lkapi, room_name: str, agent_name: str):
    try:
        response = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        for participant in response.participants:
            # Kiểm tra metadata hoặc identity, vì identity agent thường là agent_name hoặc job_id
            if participant.identity.startswith(agent_name) or (participant.metadata and agent_name in participant.metadata):
                return True
        return False
    except Exception as e:
        print(f"Error checking participants in {room_name}: {e}")
        return False

async def monitor_and_dispatch():
    lkapi = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    try:
        while True:
            try:
                response = await lkapi.room.list_rooms(api.ListRoomsRequest())
                current_time = time.time()
                
                # Xóa các phòng đã dispatch quá lâu (> 60s) để cho phép dispatch lại nếu phòng được reuse
                for room_name in list(dispatched_rooms.keys()):
                    if current_time - dispatched_rooms[room_name] > 60:
                        del dispatched_rooms[room_name]
                
                for room in response.rooms:
                    room_name = room.name
                    # Chỉ xử lý nếu phòng có user (num_participants > 0)
                    if room.num_participants > 0 and room_name not in dispatched_rooms:
                        agent_name = None
                        if room_name in PHONGKHAM_ROOMS:
                            agent_name = "phongkham_agent"
                        elif room_name in LETAN_ROOMS:
                            agent_name = "letan_agent"
                        
                        if agent_name and not await has_agent(lkapi, room_name, agent_name):
                            await dispatch_agent(room_name, agent_name)
                            await asyncio.sleep(5)  # Delay để agent join trước khi check lại
            except Exception as e:
                print(f"Error listing rooms: {e}")
            await asyncio.sleep(5)  # Kiểm tra mỗi 5 giây
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(monitor_and_dispatch())