import os
import asyncio
from dotenv import load_dotenv
from livekit import api

load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def dispatch_agent(room_name: str):
    lkapi = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    
    agent_name = "phongkham_agent" if room_name.startswith("PhongKham") else "letan_agent" if room_name.startswith("LeTan") else None
    if agent_name:
        try:
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
            )
            print(f"Dispatched {agent_name} to {room_name}")
        except Exception as e:
            print(f"Error: {e}")
    
    # (Tùy chọn) Tạo token để user join phòng
    token = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET).with_identity("user").with_grants(
        api.VideoGrants(room_join=True, room=room_name)
    ).to_jwt()
    print(f"Token to join: {token}")
    
    await lkapi.aclose()

if __name__ == "__main__":
    room_name = input("Enter room name (e.g., PhongKham01): ")
    asyncio.run(dispatch_agent(room_name))