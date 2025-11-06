import asyncio

from livekit import api 
from livekit.protocol.sip import CreateSIPParticipantRequest, SIPParticipantInfo


import asyncio
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo

load_dotenv()

async def main():
    livekit_api = api.LiveKitAPI()

    request = CreateSIPParticipantRequest(
        sip_trunk_id = "ST_Zo8PXDVbeCTT",
        sip_call_to = "3900102",
        room_name = "clinic1",
        participant_identity = "sip-test",
        participant_name = "Test Caller",
        #krisp_enabled = True,
        #wait_until_answered = True
    )
    
    try:
        participant = await livekit_api.sip.create_sip_participant(request)
        print(f"Successfully created {participant}")
    except Exception as e:
        print(f"Error creating SIP participant: {e}")
    finally:
        await livekit_api.aclose()

asyncio.run(main())