import asyncio
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable

import aiohttp
import livekit.agents as agents
from livekit.agents import WorkerOptions, JobRequest
import livekit.rtc as rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import openai, deepgram

load_dotenv()

# --------------------------
# CONFIG
# --------------------------
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
BOT_ID = "68aedccde472aa8afe432664"
TOPIC_ID = "691581fc3c870874bd64cc8f"
# --------------------------

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


async def entrypoint(ctx: agents.JobContext):
    room_name = ctx.room.name
    await ctx.connect()
    print(f"✅ Connected to room: {room_name}", flush=True)

    start_time = time.time()

    # --- Configure STT ---
    # stt=deepgram.STT(
    #         model="nova-2",
    #         language="vi",
    #         # interim_results=True,
    #         # sample_rate=16000,
    #     )
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
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                        if event.alternatives:
                            for alt in event.alternatives:
                                alt_text = getattr(alt, 'text', getattr(alt, 'transcript', '')).strip()
                                if not alt_text:
                                    continue

                                # --- Phân biệt vai trò ---
                                if "bs" in speaker.lower():
                                    # Người nói là Bác sĩ
                                    sender_id = "456"
                                    receive_id = "123"
                                    is_employee = True
                                else:
                                    # Người nói là Bệnh nhân
                                    sender_id = "123"
                                    receive_id = "456"
                                    is_employee = False

                                # --- Payload gửi webhook ---
                                webhook_msg = {
                                    "senderName": speaker,
                                    "senderId": sender_id,
                                    "receiveId": receive_id,
                                    "receiveName": "Bot",
                                    "isMessageFromEmployee": is_employee,
                                    "type": "text",
                                    "content": alt_text,
                                    "timestamp": datetime.now().isoformat(),
                                    "botId": BOT_ID,
                                    "topicId": TOPIC_ID,
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

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        print(f"[DISCONNECT] Participant {participant.identity} disconnected", flush=True)


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
        #hidden=True
    )

    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_fnc,
            agent_name="record_agent",
            permissions=worker_permissions
        )
    )
