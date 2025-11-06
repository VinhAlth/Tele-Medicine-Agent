import os
import random
import asyncio
from livekit import api
from dotenv import load_dotenv

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def main():
    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    phone_number = "+3900101"  # số muốn gọi ra
    room_name = f"phone_calling-{phone_number}"

    print(f"📞 Dispatching call to {phone_number} via room: {room_name}")

    dispatch = await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name="medical_agent_call",  # tên agent bạn đang chạy
            room=room_name,
            metadata=f'{{"phone_number": "{phone_number}"}}'
        )
    )

    print("✅ Dispatch created successfully:")
    print(dispatch)

if __name__ == "__main__":
    asyncio.run(main())
