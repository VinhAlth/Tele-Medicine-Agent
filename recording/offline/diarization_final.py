import asyncio
import re
import json
from datetime import datetime
from typing import AsyncIterable, Optional
from dotenv import load_dotenv
import os
import aiohttp
import redis

from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import deepgram
from livekit.agents import stt as agents_stt

load_dotenv()

# --- Config ---
GRAPHQL_URL = os.getenv("GRAPHQL_URL", "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql")
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
CHANNEL_ID = "68aedccde472aa8afe432664"
CUSTOMER_ID = "123"
OTHER_CUSTOMER_ID = "321"

r = redis.Redis(
    host='redis-connect.dev.longvan.vn',
    port=32276,
    password='111111aA',
    decode_responses=True
)

SPEAKER_TAG_RE = re.compile(r"\[SP(\d+)\]\s*(.*)", re.DOTALL)

# --- Redis / GraphQL helpers ---
def get_topic_id_by_room(room_name: str) -> Optional[str]:
    """Lấy topicId từ Redis theo roomName"""
    if not room_name:
        print(f"[WARN] room_name is empty", flush=True)
        return None
    try:
        value_json = r.hget("room:online", room_name)
    except Exception as e:
        print(f"[ERROR] Redis hget failed for room={room_name}: {e}", flush=True)
        return None
    if not value_json:
        print(f"[WARN] Không tìm thấy room {room_name} trong Redis", flush=True)
        return None
    try:
        data = json.loads(value_json)
        return data.get("topicId")
    except Exception as e:
        print(f"[ERROR] Lỗi decode JSON room={room_name}: {e}", flush=True)
        return None

async def assign_topic_to_doctor(room_name: str, topic_id: str) -> Optional[str]:
    """Gán assignee (doctor) cho topic, trả về doctor_id"""
    if not topic_id:
        print(f"[SKIP] Không có topicId cho room={room_name}", flush=True)
        return None
    try:
        parts = room_name.split("_")
        doctor_id = parts[1] if len(parts) > 1 else None
        if not doctor_id:
            raise ValueError("doctor_id not found in room_name pattern")
    except Exception:
        print(f"[ERROR] Không thể lấy doctorId từ room_name={room_name}", flush=True)
        return None

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
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GRAPHQL_URL, json={"query": assign_query}) as resp:
                try:
                    assign_resp = await resp.json()
                except Exception:
                    text = await resp.text()
                    print(f"[TOPIC ASSIGN FAILED] Non-JSON response: {text}", flush=True)
                    return doctor_id

                if 200 <= resp.status < 300:
                    status = assign_resp.get("data", {}).get("updateAccountableIdTopic", {}).get("status")
                    print(f"[TOPIC ASSIGNED] topic_id={topic_id} assignee_id={doctor_id} status={status}", flush=True)
                else:
                    text = await resp.text()
                    print(f"[TOPIC ASSIGN FAILED] topic_id={topic_id} assignee_id={doctor_id} status={resp.status} response={text}", flush=True)
        return doctor_id
    except Exception as e:
        print(f"[ERROR] assign_topic_to_doctor exception: {e}", flush=True)
        return doctor_id

# --- Webhook ---
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
                if 200 <= resp.status < 300:
                    print(f"[WEBHOOK OK] {message['senderName']} ➜ {message['content']}", flush=True)
                else:
                    print(f"[WEBHOOK FAILED] {message['senderName']} ➜ {message['content']} | Status: {resp.status}", flush=True)
        except Exception as e:
            print(f"[WEBHOOK ERROR] {message['senderName']} ➜ {message['content']} | Error: {e}", flush=True)

# --- Main Entrypoint ---
async def entrypoint(ctx: agents.JobContext):
    speaker_map: dict[str, str] = {}
    observed_speakers: list[str] = []
    transcript: dict[str, list[dict]] = {}
    topic_id: Optional[str] = None
    doctor_id: Optional[str] = None

    def _map_speaker(speaker_id: str) -> str:
        if speaker_id in speaker_map:
            return speaker_map[speaker_id]
        if len(observed_speakers) < 2:
            observed_speakers.append(speaker_id)
            label = "SP0" if len(observed_speakers) == 1 else "SP1"
            speaker_map[speaker_id] = label
            transcript[label] = []
            return label
        speaker_map[speaker_id] = f"Other-{speaker_id}"
        transcript[speaker_map[speaker_id]] = []
        return speaker_map[speaker_id]

    async def process_track(track: rtc.RemoteTrack, participant_name: str):
        stt_core = deepgram.STT(
            model="nova-2",
            language="vi",
            interim_results=True,
            punctuate=True,
            enable_diarization=True
        )

        multi_stt = agents_stt.MultiSpeakerAdapter(
            stt=stt_core,
            detect_primary_speaker=True,
            suppress_background_speaker=False,
            primary_format="[SP{speaker_id}] {text}",
            background_format="[SP{speaker_id}] {text}"
        )

        stt_stream = multi_stt.stream()
        audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)

        async def process_stt_stream(stream: AsyncIterable[SpeechEvent]):
            async for event in stream:
                if event.type in (SpeechEventType.INTERIM_TRANSCRIPT, SpeechEventType.FINAL_TRANSCRIPT) and event.alternatives:
                    text = event.alternatives[0].text.strip()
                    if text:
                        m = SPEAKER_TAG_RE.match(text)
                        if m:
                            spk = m.group(1)
                            body = m.group(2).strip()
                        else:
                            spk = participant_name
                            body = text
                        label = _map_speaker(spk)
                        transcript[label].append({
                            "type": "interim" if event.type == SpeechEventType.INTERIM_TRANSCRIPT else "final",
                            "participant": participant_name,
                            "text": body,
                            "time": datetime.now().isoformat()
                        })
                        print(f"[{event.type.name}] {participant_name} ({label}): {body}")

                        # --- gửi webhook nếu final ---
                        if event.type == SpeechEventType.FINAL_TRANSCRIPT and topic_id:
                            is_doctor = label == "SP0"
                            webhook_msg = {
                                "senderName": "Doctor" if is_doctor else "Patient",
                                "senderId": doctor_id if is_doctor else CUSTOMER_ID,
                                "receiveId": CUSTOMER_ID if is_doctor else doctor_id,
                                "receiveName": "Patient" if is_doctor else "Doctor",
                                "isMessageFromEmployee": is_doctor,
                                "type": "text",
                                "content": body,
                                "timestamp": datetime.now().isoformat(),
                                "botId": CHANNEL_ID,
                                "topicId": topic_id,
                                "isMessageInGroup": False
                            }
                            await send_message_to_webhook(webhook_msg)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(process_stt_stream(stt_stream))
            async for audio_event in audio_stream:
                stt_stream.push_frame(audio_event.frame)
            stt_stream.end_input()

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        sid = getattr(publication.track, "sid", "unknown") if publication else "unknown"
        print(f"[TRACK] Subscribed to {participant.identity} ({sid})")
        asyncio.create_task(process_track(track, participant.identity))

    # --- Khi connect, lấy topicId và assign doctor ---
    await ctx.connect()
    room_name = ctx.room.name
    topic_id = "691a96973c870874bd64cdd8" #get_topic_id_by_room(room_name)
    if topic_id:
        doctor_id = await assign_topic_to_doctor(room_name, topic_id)
    print("✅ Agent started, waiting for audio...")

    # --- shutdown callback ---
    async def on_shutdown():
        filename = f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        lines = []
        for speaker_id, items in transcript.items():
            for item in items:
                if item["type"] == "final":
                    time_str = datetime.fromisoformat(item["time"]).strftime("%H:%M:%S")
                    lines.append(f"{speaker_id}: {item['text']} | {time_str}")
        lines.sort(key=lambda x: x.split("|")[1].strip())
        with open(filename, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        print(f"✅ Transcript saved to {filename}")

    ctx.add_shutdown_callback(on_shutdown)

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
