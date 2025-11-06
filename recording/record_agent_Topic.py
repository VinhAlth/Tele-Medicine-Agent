import asyncio
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable
import aiohttp

import livekit.agents as agents
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
import livekit.rtc as rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import openai

load_dotenv()

WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"
BOT_ID = "68aedccde472aa8afe432664"

GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"
SOCIAL_APP_ID = "68aedccde472aa8afe432664"
SOCIAL_CHANNEL_TYPE = "AICHAT"
CUSTOMER_ID = "20.178377.7197"
ASSIGNEE_ID = "9ab78cf6-326a-491f-87fb-22f3a5a60d2b"

# --------------------------------------------
# 🔹 Tạo topic qua GraphQL
# --------------------------------------------
import aiohttp
from typing import Optional
from datetime import datetime

import aiohttp
import json

SOCIAL_APP_ID = "68aedccde472aa8afe432664"
SOCIAL_CHANNEL_TYPE = "AICHAT"
CUSTOMER_ID = "20.178377.7197"
ASSIGNEE_ID = "9ab78cf6-326a-491f-87fb-22f3a5a60d2b"
GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"

async def create_and_assign_topic(room_name: str):
    async with aiohttp.ClientSession() as session:
        # --- 1. Tạo topic ---
        create_query = f'''
        mutation createTopic {{
            createTopic(
                socialAppId: "{SOCIAL_APP_ID}", 
                socialChannelType: "{SOCIAL_CHANNEL_TYPE}", 
                customerId: "{CUSTOMER_ID}", 
                message: "{room_name}"
            ) {{
                id status name 
            }}
        }}
        '''
        async with session.post(GRAPHQL_URL, json={"query": create_query}) as resp:
            create_resp = await resp.json()
            if resp.status >= 200 and resp.status < 300:
                topic_data = create_resp.get("data", {}).get("createTopic", {})
                topic_id = topic_data.get("id")
                status = topic_data.get("status")
                print(f"[TOPIC CREATED] room={room_name} topic_id={topic_id} status={status}")
                print(f"[TOPIC API RESPONSE] {json.dumps(create_resp, ensure_ascii=False)}")
            else:
                text = await resp.text()
                print(f"[TOPIC CREATE FAILED] room={room_name} status={resp.status} response={text}")
                return  # Không tiếp tục nếu tạo topic thất bại

        # --- 2. Gán trách nhiệm ---
        assign_query = f'''
        mutation updateAccountableIdTopic {{
            updateAccountableIdTopic(
                topicId: "{topic_id}", 
                assigneeId: "{ASSIGNEE_ID}"
            ) {{
                id status 
            }}
        }}
        '''
        async with session.post(GRAPHQL_URL, json={"query": assign_query}) as resp:
            assign_resp = await resp.json()
            if resp.status >= 200 and resp.status < 300:
                assign_data = assign_resp.get("data", {}).get("updateAccountableIdTopic", {})
                status = assign_data.get("status")
                print(f"[TOPIC ASSIGNED] topic_id={topic_id} assignee_id={ASSIGNEE_ID} status={status}")
                print(f"[ASSIGN API RESPONSE] {json.dumps(assign_resp, ensure_ascii=False)}")
            else:
                text = await resp.text()
                print(f"[TOPIC ASSIGN FAILED] topic_id={topic_id} assignee_id={ASSIGNEE_ID} status={resp.status} response={text}")


# --------------------------------------------
# 🔹 Gửi message webhook
# --------------------------------------------
async def send_message_to_webhook(message: dict):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=message) as resp:
                if 200 <= resp.status < 300:
                    print(f"[WEBHOOK SENT] {message['senderName']} ➜ {message['content']}", flush=True)
                else:
                    text = await resp.text()
                    print(f"[WEBHOOK FAILED] {message['senderName']} ➜ {message['content']} | Status: {resp.status} | {text}", flush=True)
        except Exception as e:
            print(f"[WEBHOOK ERROR] {message['senderName']} ➜ {message['content']} | Error: {e}", flush=True)

# --------------------------------------------
# 🔹 Entry point chính của Agent
# --------------------------------------------
async def entrypoint(ctx: agents.JobContext):
    room_name = ctx.room.name

    await ctx.connect()
    print(f"✅ Connected to room: {room_name}", flush=True)

    # Khi room bắt đầu, tạo topic
    topic_info = await create_and_assign_topic(room_name)

    start_time = time.time()

    stt = openai.STT(
        model="gpt-4o-mini-transcribe",
        language="vi",
    )

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
                    # chỉ gửi final transcript
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                        for alt in event.alternatives:
                            alt_text = getattr(alt, 'text', getattr(alt, 'transcript', ''))
                            webhook_msg = {
                                "senderName": speaker,
                                "senderId": "09029292222",
                                "receiveId": "Bot",
                                "receiveName": "Bot",
                                "role": False,
                                "type": "text",
                                "content": alt_text,
                                "timestamp": datetime.now().isoformat(),
                                "botId": BOT_ID,
                                "status": 1,
                                "meta": {"is_final": True}
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

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        print(f"[DISCONNECT] Participant {participant.identity} disconnected", flush=True)

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
