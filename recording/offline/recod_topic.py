# agent_with_topic_and_webhook.py
import os
import json
import asyncio
from datetime import datetime
from typing import AsyncIterable, Optional

from dotenv import load_dotenv
import aiohttp
import redis

from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import deepgram

load_dotenv()

# ---------------------------
# Configs (sửa env nếu cần)
# ---------------------------
GRAPHQL_URL = os.getenv("GRAPHQL_URL", "https://com-hub.dev.longvan.vn/graphql")
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
# Channel/BOT id (the same as BOT_ID theo yêu cầu)
CHANNEL_ID = "68aedccde472aa8afe432664"

# Customer fake IDs (giữ đơn giản như bạn yêu cầu)
CUSTOMER_ID = "123"
OTHER_CUSTOMER_ID = "321"

# Redis client (synchronous redis-py client) - dùng thông tin bạn cung cấp
r = redis.Redis(
    host='redis-connect.dev.longvan.vn',
    port=32276,
    password='111111aA',
    decode_responses=True
)

# ---------------------------
# Helper utilities
# ---------------------------
def clean_participant_name(identity: str) -> str:
    """
    Loại bỏ phần suffix dạng __xxxx để lấy tên đẹp
    Ví dụ: "NguyenQuocVinh__73gb" -> "NguyenQuocVinh"
    """
    if not identity:
        return identity
    return identity.split("__")[0]

def is_doctor_identity(name: str) -> bool:
    """
    Rule: bắt đầu bằng 'bs.' (case-insensitive)
    Ví dụ: 'bs.Binh' => True
    """
    if not name:
        return False
    return name.lower().startswith("bs.")

def get_room_name_from_ctx(ctx: agents.JobContext) -> Optional[str]:
    """
    Thử lấy room name từ ctx.room bằng vài thuộc tính khả dĩ.
    """
    room = getattr(ctx, "room", None)
    if not room:
        return None
    # Thử các thuộc tính phổ biến
    for attr in ("name", "room_name", "sid", "id"):
        val = getattr(room, attr, None)
        if val:
            return val
    # fallback: None
    return None

def get_other_participant_identity(ctx: agents.JobContext, speaker_identity: str) -> Optional[str]:
    """
    Tìm participant khác trong room (nếu có) và trả về identity của họ.
    """
    room = getattr(ctx, "room", None)
    if not room:
        return None

    participants = []
    # ctx.room.participants có thể là dict hoặc list tùy SDK
    part_attr = getattr(room, "participants", None)
    if part_attr is None:
        return None

    # Nếu là dict-like
    try:
        # some SDK expose mapping of sid -> participant object
        items = list(part_attr.values()) if hasattr(part_attr, "values") else list(part_attr)
    except Exception:
        items = list(part_attr) if part_attr else []

    for p in items:
        # p có thể là object hoặc str; cố lấy identity attribute nếu có
        if p is None:
            continue
        identity = getattr(p, "identity", None) or (p if isinstance(p, str) else None)
        if identity and identity != speaker_identity:
            return identity
    return None

def get_single_participant_user_label(ctx: agents.JobContext, speaker_identity: str) -> str:
    """
    Trường hợp chỉ 1 participant trong room:
    - Nếu Deepgram detect nhiều người (speaker 1, speaker 2...) thì đánh label user1, user2
    - Ở đây tạm thời chỉ dùng speaker_identity để gán user1
    """
    room = getattr(ctx, "room", None)
    participants = getattr(room, "participants", None)
    count = 0
    if participants:
        try:
            items = list(participants.values()) if hasattr(participants, "values") else list(participants)
            count = len(items)
        except Exception:
            count = 1
    if count == 1:
        # label đơn giản: user1
        return f"user1 ({clean_participant_name(speaker_identity)})"
    return clean_participant_name(speaker_identity)

# ---------------------------
# Redis / Topic helpers
# ---------------------------
def get_topic_id_by_room(room_name: str) -> Optional[str]:
    """Lấy topicId từ Redis theo roomName (synchronous)"""
    if not room_name:
        print(f"[WARN] room_name is empty", flush=True)
        return None
    hash_key = "room:online"
    try:
        value_json = r.hget(hash_key, room_name)
    except Exception as e:
        print(f"[ERROR] Redis hget failed for room={room_name}: {e}", flush=True)
        return None

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

async def assign_topic_to_doctor(room_name: str, topic_id: str):
    """
    Gán assignee (doctor) cho topic. DoctorId lấy từ room_name theo pattern prefix_<doctorId>_...
    Nếu không tìm được doctorId, sẽ in ra lỗi.
    Gọi 1 lần khi agent vào room.
    """
    if not topic_id:
        print(f"[SKIP] Không có topicId cho room={room_name}", flush=True)
        return

    try:
        # giả sử room_name dạng: prefix_<doctorId>_...
        parts = room_name.split("_")
        doctor_id = parts[1] if len(parts) > 1 else None
        if not doctor_id:
            raise ValueError("doctor_id not found in room_name pattern")
    except Exception:
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
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GRAPHQL_URL, json={"query": assign_query}) as resp:
                try:
                    assign_resp = await resp.json()
                except Exception:
                    text = await resp.text()
                    print(f"[TOPIC ASSIGN FAILED] Non-JSON response: {text}", flush=True)
                    return

                if 200 <= resp.status < 300:
                    status = assign_resp.get("data", {}).get("updateAccountableIdTopic", {}).get("status")
                    print(f"[TOPIC ASSIGNED] topic_id={topic_id} assignee_id={doctor_id} status={status}", flush=True)
                else:
                    text = await resp.text()
                    print(f"[TOPIC ASSIGN FAILED] topic_id={topic_id} assignee_id={doctor_id} status={resp.status} response={text}", flush=True)
    except Exception as e:
        print(f"[ERROR] assign_topic_to_doctor exception: {e}", flush=True)

# ---------------------------
# Webhook sending
# ---------------------------
async def send_chat_webhook(ctx: agents.JobContext, room_name: str, topic_id: Optional[str],
                            speaker_identity: str, alt_text: str):
    """
    Gửi webhook cho mỗi FINAL transcript.
    Xác định sender/receiver theo rule:
      - Nếu speaker là BS (tên bắt đầu 'bs.'), senderId = doctor_id, receiverId = CUSTOMER_ID
      - Nếu người nói khác (không BS) và other là BS, senderId = CUSTOMER_ID, receiverId = doctor_id
      - Nếu không có ai là BS, dùng CUSTOMER_ID / OTHER_CUSTOMER_ID theo participant order
    """
    # chuẩn bị thông tin names (bỏ phần __suffix)
    speaker_clean = clean_participant_name(speaker_identity)
    other_identity = get_other_participant_identity(ctx, speaker_identity)
    other_clean = clean_participant_name(other_identity) if other_identity else None

    # lấy topicId (nếu chưa có) - cho chắc, dùng tham số topic_id
    topic_id_to_use = topic_id

    # try extract doctor_id from room_name pattern if possible
    doctor_id = None
    if room_name:
        try:
            parts = room_name.split("_")
            if len(parts) > 1:
                doctor_id = parts[1]
        except Exception:
            doctor_id = None

    speaker_is_bs = is_doctor_identity(speaker_clean)
    other_is_bs = is_doctor_identity(other_clean) if other_clean else False

    # Default receiver/sender ids/names
    sender_id = CUSTOMER_ID
    receive_id = OTHER_CUSTOMER_ID
    sender_name = speaker_clean
    receive_name = other_clean or "Unknown"

    # Determine ids/names based on rules
    if speaker_is_bs:
        # speaker is doctor
        if doctor_id:
            sender_id = doctor_id
        else:
            sender_id = CUSTOMER_ID  # fallback (shouldn't happen if assign worked)
        receive_id = CUSTOMER_ID
        receive_name = other_clean or "Customer"
    elif other_is_bs:
        # other participant is doctor, speaker is customer
        sender_id = CUSTOMER_ID
        receive_id = doctor_id if doctor_id else CUSTOMER_ID
        receive_name = other_clean or "Bs"
    else:
        # none is BS -> map 123 <> 321 by participant ordering
        # Build deterministic ordering so that mapping stable
        identities = []
        room = getattr(ctx, "room", None)
        if room:
            part_attr = getattr(room, "participants", None)
            try:
                items = list(part_attr.values()) if hasattr(part_attr, "values") else list(part_attr)
            except Exception:
                items = list(part_attr) if part_attr else []
            for p in items:
                identity = getattr(p, "identity", None) or (p if isinstance(p, str) else None)
                if identity:
                    identities.append(clean_participant_name(identity))
        # fallback: use speaker/other
        if not identities:
            # if we have other_clean then two participants known
            if other_clean:
                identities = [speaker_clean, other_clean]
            else:
                identities = [speaker_clean]

        # map first -> CUSTOMER_ID, second -> OTHER_CUSTOMER_ID
        if identities and identities[0] == speaker_clean:
            sender_id = CUSTOMER_ID
            receive_id = OTHER_CUSTOMER_ID
            receive_name = other_clean or "Other"
        else:
            sender_id = OTHER_CUSTOMER_ID
            receive_id = CUSTOMER_ID
            receive_name = other_clean or "Other"

    # Detect if message is from employee
    is_message_from_employee = speaker_is_bs

    webhook_msg = {
        "senderName": sender_name,
        "senderId": sender_id,
        "receiveId": receive_id,
        "receiveName": receive_name,
        "isMessageFromEmployee": is_message_from_employee,
        "type": "text",
        "content": alt_text,
        "timestamp": datetime.now().isoformat(),
        "botId": CHANNEL_ID,
        "topicId": "691581fc3c870874bd64cc8f",
        "isMessageInGroup": False,
    }

    # Send to webhook
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=webhook_msg) as resp:
                text = await resp.text()
                if 200 <= resp.status < 300:
                    print(f"[WEBHOOK SENT] speaker={speaker_clean} text='{alt_text[:50]}' status={resp.status}", flush=True)
                else:
                    print(f"[WEBHOOK FAILED] status={resp.status} resp={text}", flush=True)
    except Exception as e:
        print(f"[ERROR] send_chat_webhook exception: {e}", flush=True)

# ---------------------------
# Main agent entrypoint
# ---------------------------
async def entrypoint(ctx: agents.JobContext):
    """
    - Connect agent
    - Khi vào room: lấy room_name -> lấy topicId từ Redis -> assign topic -> start lắng nghe track
    - Với mỗi track, tạo task process_track để gửi final transcripts về webhook
    """
    # try to connect first (the original code awaited ctx.connect() after setup)
    await ctx.connect()

    # Get room name (try multiple attributes)
    room_name = get_room_name_from_ctx(ctx) or ""
    print(f"[INFO] Agent joined room: {room_name}", flush=True)

    # Get topicId from Redis
    topic_id = get_topic_id_by_room(room_name)
    if topic_id:
        # assign to doctor once
        asyncio.create_task(assign_topic_to_doctor(room_name, topic_id))
    else:
        print(f"[WARN] topic_id not found for room={room_name}", flush=True)

    # Handler when participant's track is subscribed
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        """
        Called when we subscribe to a participant's track.
        - track: rtc.RemoteTrack
        - participant.identity available (string)
        """
        speaker_identity = getattr(participant, "identity", None) or str(participant)
        print(f"[TRACK] Subscribed to audio from participant: {speaker_identity}", flush=True)
        # Create a background task to process this participant's track
        asyncio.create_task(process_track(track, speaker_identity))

    async def process_track(track: rtc.RemoteTrack, speaker: str):
        stt = deepgram.STT(
            model="nova-2",
            language="vi",
        )
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        async def pump_audio():
            try:
                async for audio_event in audio_stream:
                    try:
                        stt_stream.push_frame(audio_event.frame)
                    except Exception as e:
                        print(f"[ERROR] push_frame failed for {speaker}: {e}", flush=True)
                    await asyncio.sleep(0)   # tránh nghẽn event loop
            except Exception as e:
                print(f"[ERROR] pump_audio crashed for {speaker}: {e}", flush=True)
            finally:
                print(f"[AUDIO END] {speaker} audio_stream ended", flush=True)
                try:
                    stt_stream.end_input()
                except:
                    pass

        async def pump_stt():
            try:
                async for event in stt_stream:
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                        text = event.alternatives[0].text.strip()
                        if text:
                            print(f"[FINAL] {speaker}: {text}", flush=True)
                            asyncio.create_task(send_chat_webhook(
                                ctx, room_name, topic_id, speaker, text
                            ))
                    elif event.type == SpeechEventType.INTERIM_TRANSCRIPT:
                        text = event.alternatives[0].text.strip()
                        if text:
                            print(f"[INTERIM] {speaker}: {text}", flush=True)
                    await asyncio.sleep(0)
            except Exception as e:
                print(f"[ERROR] pump_stt crashed for {speaker}: {e}", flush=True)
            finally:
                print(f"[STT END] {speaker} stt_stream finished", flush=True)

        # chạy song song, không dùng TaskGroup (tránh deadlock)
        asyncio.create_task(pump_audio())
        asyncio.create_task(pump_stt())


    print("✅ Agent started, waiting for audio...", flush=True)
    # keep running
    while True:
        await asyncio.sleep(1)

# ---------------------------
# Entrypoint runner
# ---------------------------
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
