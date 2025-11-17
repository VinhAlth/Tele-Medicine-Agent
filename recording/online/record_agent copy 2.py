import asyncio
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable
import aiohttp
from livekit.api import ListParticipantsRequest
import livekit.agents as agentsQF
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
import livekit.rtc as rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import openai
import livekit.agents as agents
load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"
BOT_ID = "68aedccde472aa8afe432664"
SOCIAL_APP_ID = "68aedccde472aa8afe432664"
SOCIAL_CHANNEL_TYPE = "AICHAT"
FIXED_TOPIC_ID = "6909d166a71ad2552a05723a"  # Topic cố định    

# --------------------------------------------
# 🔹 Gán trách nhiệm theo doctorId trong room name
# --------------------------------------------


# --------------------------------------------
# 🔹 Gửi message webhook
# --------------------------------------------
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
BOT_ID = "68aedccde472aa8afe432664"
FIXED_SENDER_ID = "09029293333"
FIXED_RECEIVE_ID = "09029292222"


async def send_message_to_webhook(message: dict):
    async with aiohttp.ClientSession() as session:
        try:
            print("\n====================== [SEND WEBHOOK] ======================", flush=True)
            print(json.dumps(message, ensure_ascii=False, indent=2), flush=True)
            print("===========================================================\n", flush=True)

            async with session.post(WEBHOOK_URL, json=message) as resp:
                resp_text = await resp.text()
                print(f"[WEBHOOK RESPONSE] Status: {resp.status}", flush=True)
                print(resp_text, flush=True)
                print("===========================================================\n", flush=True)

                if 200 <= resp.status < 300:
                    print(f"[WEBHOOK OK] {message['senderName']} ➜ {message['content']}", flush=True)
                else:
                    print(f"[WEBHOOK FAILED] {message['senderName']} ➜ {message['content']} | Status: {resp.status}", flush=True)
        except Exception as e:
            print(f"[WEBHOOK ERROR] {message['senderName']} ➜ {message['content']} | Error: {e}", flush=True)

import asyncio
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable
import aiohttp
import redis, json

from livekit.api import ListParticipantsRequest
import livekit.agents as agentsQF
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
import livekit.rtc as rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import openai
import livekit.agents as agents
load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"
BOT_ID = "68aedccde472aa8afe432664"

# -----------------------------
# 🔹 Redis setup
# -----------------------------
r = redis.Redis(
    host='redis-connect.dev.longvan.vn',
    port=32276,
    password='111111aA',
    decode_responses=True
)

def get_topic_id_by_room(room_name: str) -> str | None:
    """Lấy topicId từ Redis theo roomName"""
    hash_key = "room:online"
    value_json = r.hget(hash_key, room_name)
    if not value_json:
        print(f"[WARN] Không tìm thấy room {room_name} trong Redis", flush=True)
        return None
    try:
        data = json.loads(value_json)
        topic_id = data.get("topicId")
        return topic_id
    except Exception as e:
        print(f"[ERROR] Lỗi decode JSON room={room_name}: {e}", flush=True)
        return None

# --------------------------------------------
# 🔹 Gán trách nhiệm theo doctorId trong room name
# --------------------------------------------
async def assign_topic_to_doctor(room_name: str, topic_id: str):
    if not topic_id:
        print(f"[SKIP] Không có topicId cho room={room_name}", flush=True)
        return

    try:
        doctor_id = room_name.split("_")[1]
    except IndexError:
        print(f"[ERROR] Không thể lấy doctorId từ room_name={room_name}", flush=True)
        return

    assign_query = f'''
    mutation updateAccountableIdTopic {{
        updateAccountableIdTopic(
            topicId: "{topic_id}", 
            assigneeId: "{doctor_id}"
        ) {{
            id status 
        }}
    }}
    '''
    async with aiohttp.ClientSession() as session:
        async with session.post(GRAPHQL_URL, json={"query": assign_query}) as resp:
            assign_resp = await resp.json()
            if 200 <= resp.status < 300:
                status = assign_resp.get("data", {}).get("updateAccountableIdTopic", {}).get("status")
                print(f"[TOPIC ASSIGNED] topic_id={topic_id} assignee_id={doctor_id} status={status}", flush=True)
            else:
                text = await resp.text()
                print(f"[TOPIC ASSIGN FAILED] topic_id={topic_id} assignee_id={doctor_id} status={resp.status} response={text}", flush=True)

async def close_topic(topic_id: str):
    if not topic_id:
        return
    close_query = f'''
    mutation closeTopic {{
        closeTopic(id: "{topic_id}") {{
            status
        }}
    }}
    '''
    async with aiohttp.ClientSession() as session:
        async with session.post(GRAPHQL_URL, json={"query": close_query}) as resp:
            close_resp = await resp.json()
            if 200 <= resp.status < 300:
                status = close_resp.get("data", {}).get("closeTopic", {}).get("status")
                print(f"[TOPIC CLOSED] topic_id={topic_id} status={status}", flush=True)
            else:
                text = await resp.text()
                print(f"[TOPIC CLOSE FAILED] topic_id={topic_id} status={resp.status} response={text}", flush=True)

# --------------------------------------------
# 🔹 Entry point chính của Agent
# --------------------------------------------
async def entrypoint(ctx: agents.JobContext):
    room_name = ctx.room.name
    await ctx.connect()
    print(f"✅ Connected to room: {room_name}", flush=True)

    # 🔹 Lấy topicId từ Redis
    topic_id = get_topic_id_by_room(room_name)

    # 🔹 Gán topic cho doctor
    await assign_topic_to_doctor(room_name, topic_id)

    # Check số người realtime
    current_participants = 4

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        nonlocal current_participants
        current_participants += 1
        print(f"[CONNECTED] {participant.identity}, total={current_participants}", flush=True)

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        nonlocal current_participants
        current_participants = max(0, current_participants - 1)
        print(f"[DISCONNECT] {participant.identity}, total={current_participants}", flush=True)
        if current_participants < 4:
            asyncio.create_task(close_topic(topic_id))

    start_time = time.time()
    stt = openai.STT(model="gpt-4o-transcribe", language="vi")

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"[TRACK] Subscribed to audio from participant: {participant.identity}", flush=True)
            asyncio.create_task(process_track(track, participant.identity, start_time))

    async def process_track(track: rtc.RemoteTrack, speaker: str, stream_start: float):
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        async def process_stt_stream(stream: AsyncIterable[SpeechEvent]):
            try:
                async for event in stream:
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                        for alt in event.alternatives:
                            alt_text = getattr(alt, 'text', getattr(alt, 'transcript', ''))
                            process_time = round(time.time() - stream_start, 2)
                            print("\n====================== [AI STT RESULT] ======================", flush=True)
                            print(f"👤 Speaker: {speaker}")
                            print(f"🕒 Time since start: {process_time}s")
                            print(f"💬 Transcript: {alt_text}")
                            print("===========================================================\n", flush=True)

                            is_employee = "bác sĩ" in speaker.lower() or "bs" in speaker.lower()
                            topic_id = get_topic_id_by_room(ctx.room.name) or FIXED_TOPIC_ID
                            webhook_msg = {
                                "senderName": speaker,
                                "senderId": FIXED_SENDER_ID,
                                "receiveId": FIXED_RECEIVE_ID,
                                "receiveName": "bệnh nhân",
                                "isMessageFromEmployee": is_employee,
                                "type": "text",
                                "content": alt_text,
                                "timestamp": datetime.now().isoformat(),
                                "botId": BOT_ID,
                                "topicId": topic_id,
                                "isMessageInGroup": False
                            }
                            await send_message_to_webhook(webhook_msg)
            except Exception as e:
                print(f"[STT ERROR] {speaker}: {e}", flush=True)
            finally:
                await stream.aclose()

        async with asyncio.TaskGroup() as tg:
            stt_task = tg.create_task(process_stt_stream(stt_stream))
            async for event in audio_stream:
                stt_stream.push_frame(event.frame)
            stt_stream.end_input()
            await stt_task

# --------------------------------------------
# 🔹 Worker setup
# --------------------------------------------
async def request_fnc(req: JobRequest) -> None:
    await req.accept(
        name="Trợ lý khám bệnh",
        identity="record_agent",
    )

if __name__ == "__main__":
    worker_permissions = agents.WorkerPermissions(
        can_publish=False,
        can_subscribe=True,
        can_publish_data=True,
        hidden=True
    )

    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_fnc,
            agent_name="record_agent",
            permissions=worker_permissions
        )
    )
