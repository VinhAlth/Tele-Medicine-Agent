import os
import asyncio
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo

load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

async def main():
    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    trunk = SIPOutboundTrunkInfo(
        name="TeleMedician Outbound",
        address="45.119.85.50:5060",  # SIP server IP
        numbers=["3900102"],                 # Cho phép gọi từ mọi số
        auth_username="103",           # Username SIP ST_ZnisXiBV2G8Z
        auth_password="123456"         # Password SIP
    )

    request = CreateSIPOutboundTrunkRequest(trunk=trunk)

    print("🚀 Creating outbound trunk...")
    created = await lkapi.sip.create_sip_outbound_trunk(request)
    print("✅ Outbound trunk created successfully:")
    print(created)

    await lkapi.aclose()

asyncio.run(main())
