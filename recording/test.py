import asyncio
import json
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable, List, Dict, Any

import aiohttp
import livekit.agents as agents
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
import livekit.rtc as rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import google, openai

load_dotenv()

WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
BOT_ID = "68aedccde472aa8afe432664"

# Global list to collect transcripts
transcripts: List[Dict[str, Any]] = []

# Danh sách phòng cố định được phép join
ALLOWED_ROOMS = [f"PhongKham{i:02}" for i in range(1, 7)]  # PhongKham01 -> PhongKham06

async def send_messages_to_webhook(messages: List[Dict[str, Any]]):
    async with aiohttp.ClientSession() as session:
        for msg in messages:
            sender_name = msg["speaker"]
            receiver_name = "Bot" if sender_name != "Bot" else "user"
            content_str = msg["text"]
            body = {
                "senderName": sender_name,
                "senderId": "09029292222" if sender_name != "Bot" else "Bot",
                "receiveId": "Bot" if sender_name != "Bot" else "09029292222",
                "receiveName": receiver_name,
                "role": True,
                "type": "text",
                "content": content_str,
                "timestamp": msg["timestamp"],
                "botId": BOT_ID,
                "status": 1
            }
            try:
                async with session.post(WEBHOOK_URL, json=body) as resp:
                    if 200 <= resp.status < 300:
                        print(f"Sent message from {sender_name} successfully")
                    else:
                        print(f"Failed to send message from {sender_name}, status: {resp.status}")
            except Exception as e:
                print(f"Error sending message from {sender_name}: {e}")
            await asyncio.sleep(0.1)

async def entrypoint(ctx: agents.JobContext):
    room_name = ctx.room.name

    # # Kiểm tra phòng có được phép join không
    # if room_name in ALLOWED_ROOMS:
    #     print(f"Room '{room_name}' is not in allowed rooms, skipping join.")
    #     return

    # Connect to the room
    await ctx.connect()
    print(f"Connected to allowed room: {room_name}")

    start_time = time.time()

    # Configure Google STT
    stt= openai.STT(
        model="gpt-4o-mini-transcribe",  # Hoặc "whisper-1" nếu muốn
        language="vi",                  # Tiếng Việt
    )

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"Subscribed to audio track from participant: {participant.identity}")
            asyncio.create_task(process_track(track, participant.identity, start_time))

    async def process_track(track: rtc.RemoteTrack, speaker: str, stream_start: float):
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        async def process_stt_stream(stream: AsyncIterable[SpeechEvent]):
            try:
                async for event in stream:
                    if event.type in (SpeechEventType.FINAL_TRANSCRIPT, SpeechEventType.INTERIM_TRANSCRIPT):
                        if event.alternatives:
                            is_final = event.type == SpeechEventType.FINAL_TRANSCRIPT
                            for alt in event.alternatives:
                                alt_text = alt.text if hasattr(alt, 'text') else alt.transcript
                                entry = {
                                    "speaker": speaker,
                                    "start_time": alt.start_time + stream_start,
                                    "end_time": alt.end_time + stream_start,
                                    "text": alt_text,
                                    "is_final": is_final,
                                    "timestamp": datetime.fromtimestamp(time.time()).isoformat()
                                }
                                transcripts.append(entry)
                                if is_final:
                                    print(f"[{speaker}] [FINAL] {alt_text}")
                                else:
                                    print(f"[{speaker}] [INTERIM] {alt_text}")
                        else:
                            print(f"[{speaker}] No alternatives in event")
            except Exception as e:
                print(f"STT error for {speaker}: {e}")
            finally:
                await stream.aclose()

        async with asyncio.TaskGroup() as tg:
            stt_task = tg.create_task(process_stt_stream(stt_stream))

            async for event in audio_stream:
                frame = event.frame
                stt_stream.push_frame(frame)

            stt_stream.end_input()
            await stt_task

    # Shutdown hook: Save JSON and send messages
    async def save_transcript():
        print("Shutting down and saving transcripts...")
        if transcripts:
            filename = f"/home/redknight/voice_agent_stt/STT/transcript_{room_name}_{int(time.time())}.json"
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(transcripts, f, ensure_ascii=False, indent=2)
            print(f"Saved transcript to {filename}")

            await send_messages_to_webhook(transcripts)
            transcripts.clear()
        else:
            print("No transcripts to save")

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        print(f"Participant {participant.identity} disconnected")
        if len(ctx.room.remote_participants) == 0:
            print("All participants left, saving transcript...")
            asyncio.create_task(save_transcript())


async def request_fnc(req: JobRequest) -> None:
    # Luôn chấp nhận request và đặt thông tin agent
    await req.accept(
        name="Trợ lý khám bệnh",     # tên hiển thị trong room
        identity="record_agent",    # định danh agent
        # attributes={"role": "assistant"}  # tuỳ chọn thêm nếu cần
    )
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest, WorkerPermissions
if __name__ == "__main__":
    from livekit import agents
    
    # Define agent permissions with hidden=True
    worker_permissions = WorkerPermissions(
        can_publish=False, # Hidden agents cannot publish tracks
        can_subscribe=True,
        can_publish_data=True, # Allow data channel communication
        hidden=True # Set the agent as hidden
    )

    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_fnc,
            agent_name="record_agent",  
            permissions=worker_permissions # Apply the custom permissions
        )
    )

# -------------------------
# Chạy Worker
