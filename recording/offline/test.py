# agent_with_topic_and_webhook_diarization.py
import os
import json
import asyncio
from datetime import datetime
from typing import AsyncIterable, Optional, Any

from dotenv import load_dotenv
import aiohttp
import redis

from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent, MultiSpeakerAdapter
from livekit.plugins import deepgram

load_dotenv()

# ---------------------------
# Configs (sửa env nếu cần)
# ---------------------------
GRAPHQL_URL = os.getenv("GRAPHQL_URL", "https://com-hub.dev.longvan.vn/graphql")
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
# Channel/BOT id (the same as BOT_ID theo yêu cầu)
CHANNEL_ID = os.getenv("CHANNEL_ID", "68aedccde472aa8afe432664")

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
def clean_participant_name(identity: Optional[str]) -> str:
    """
    Loại bỏ phần suffix dạng __xxxx để lấy tên đẹp
    Ví dụ: "NguyenQuocVinh__73gb" -> "NguyenQuocVinh"
    """
    if not identity:
        return ""
    return identity.split("__")[0]

def is_doctor_identity(name: Optional[str]) -> bool:
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
    for attr in ("name", "room_name", "sid", "id"):
        val = getattr(room, attr, None)
        if val:
            return val
    return None

def get_other_participant_identity(ctx: agents.JobContext, speaker_identity: str) -> Optional[str]:
    """
    Tìm participant khác trong room (nếu có) và trả về identity của họ.
    """
    room = getattr(ctx, "room", None)
    if not room:
        return None

    part_attr = getattr(room, "participants", None)
    if part_attr is None:
        return None

    try:
        items = list(part_attr.values()) if hasattr(part_attr, "values") else list(part_attr)
    except Exception:
        items = list(part_attr) if part_attr else []

    for p in items:
        if p is None:
            continue
        identity = getattr(p, "identity", None) or (p if isinstance(p, str) else None)
        if identity and identity != speaker_identity:
            return identity
    return None

def count_room_participants(ctx: agents.JobContext) -> int:
    """
    Trả về số lượng participant hiện tại trong room (nếu không lấy được trả 0)
    """
    room = getattr(ctx, "room", None)
    if not room:
        return 0
    part_attr = getattr(room, "participants", None)
    if part_attr is None:
        return 0
    try:
        # dict-like or list-like
        return len(list(part_attr.values()) if hasattr(part_attr, "values") else list(part_attr))
    except Exception:
        try:
            return len(part_attr)
        except Exception:
            return 0

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
                            sender_name: str, sender_id: str, receive_name: str, receive_id: str,
                            is_message_from_employee: bool, alt_text: str):
    """
    Gửi webhook cho mỗi FINAL transcript.
    Hàm này nhận đủ thông tin sender/receiver để linh hoạt (đặc biệt khi dùng diarization).
    """
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
        "topicId": topic_id or "",
        "isMessageInGroup": False,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=webhook_msg) as resp:
                text = await resp.text()
                if 200 <= resp.status < 300:
                    print(f"[WEBHOOK SENT] sender={sender_name} text='{alt_text[:50]}' status={resp.status}", flush=True)
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
    await ctx.connect()

    room_name = get_room_name_from_ctx(ctx) or ""
    print(f"[INFO] Agent joined room: {room_name}", flush=True)

    topic_id = get_topic_id_by_room(room_name)
    if topic_id:
        asyncio.create_task(assign_topic_to_doctor(room_name, topic_id))
    else:
        print(f"[WARN] topic_id not found for room={room_name}", flush=True)

    # Handler khi subscribe track
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        speaker_identity = getattr(participant, "identity", None) or str(participant)
        print(f"[TRACK] Subscribed to audio from participant: {speaker_identity}", flush=True)
        asyncio.create_task(process_track(track, speaker_identity))

    async def process_track(track: rtc.RemoteTrack, speaker: str):
        """
        Xử lý audio -> push vào STT stream -> lắng nghe events -> gửi FINAL qua webhook.

        Bổ sung:
         - Nếu trong room chỉ có 1 participant -> bật diarization để Deepgram cố tách nhiều speaker trong cùng 1 mic.
           Dùng MultiSpeakerAdapter để support single audio track multi-speaker.
         - Nếu >=2 participants -> giữ hành vi trước (stt bình thường).
        """
        # Kiểm tra số lượng participant hiện có
        participant_count = count_room_participants(ctx)
        # Nếu chỉ có 1 participant, bật diarization
        enable_diarization = (participant_count == 1)
        if enable_diarization:
            print(f"[INFO] Single participant in room -> enabling diarization for track={speaker}", flush=True)
            # Tạo STT với diarization bật
            stt_base = deepgram.STT(
                model="nova-2",
                language="vi",
                enable_diarization=True,
            )
            # MultiSpeakerAdapter giúp xử lý nhiều speaker trên 1 audio track
            stt_adapter = MultiSpeakerAdapter(stt=stt_base)
            # Lấy stream từ adapter
            stt_stream = stt_adapter.stream()
        else:
            # Hành vi cũ
            # stt = deepgram.STT(
            #     model="nova-2",
            #     language="vi",
            #     enable_diarization=False,
            # )
            # stt_stream = stt.stream()
            stt_base = deepgram.STT(model="nova-2", language="vi", enable_diarization=True)
            stt_stream = MultiSpeakerAdapter(stt=stt_base).stream()
            
        audio_stream = rtc.AudioStream(track)

        # mapping cho diarization: speaker_tag -> "User N"
        diar_map: dict[str, str] = {}
        diar_counter = 0

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
                except Exception:
                    pass

        async def pump_stt():
            nonlocal diar_counter
            try:
                async for event in stt_stream:
                    # event.alternatives có thể chứa 1 hoặc nhiều SpeechData (tùy provider / diarization)
                    alts = getattr(event, "alternatives", None) or []
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                        # nếu diarization bật, cố gắng lấy speaker_id từ each alternative
                        for alt in alts:
                            # lấy text
                            text = ""
                            if hasattr(alt, "text"):
                                text = getattr(alt, "text") or ""
                            elif isinstance(alt, dict):
                                text = alt.get("text", "") or ""
                            # try speaker id keys (defensive)
                            speaker_tag = None
                            if hasattr(alt, "speaker_id"):
                                speaker_tag = getattr(alt, "speaker_id", None)
                            elif isinstance(alt, dict):
                                # common keys
                                speaker_tag = alt.get("speaker_id") or alt.get("speaker") or alt.get("speaker_tag")
                            # Nếu không có speaker_tag (ví dụ khi không diarization), fallback về participant identity
                            if enable_diarization and speaker_tag:
                                # map speaker_tag -> User N
                                if str(speaker_tag) not in diar_map:
                                    diar_counter += 1
                                    diar_map[str(speaker_tag)] = f"User {diar_counter}"
                                speaker_label = diar_map[str(speaker_tag)]
                                # map ids: user1 -> CUSTOMER_ID, user2 -> OTHER_CUSTOMER_ID, others -> OTHER_CUSTOMER_ID
                                if diar_map[str(speaker_tag)] == "User 1":
                                    sender_id = CUSTOMER_ID
                                    receive_id = OTHER_CUSTOMER_ID
                                else:
                                    sender_id = OTHER_CUSTOMER_ID
                                    receive_id = CUSTOMER_ID
                                sender_name = speaker_label
                                receive_name = "Other"
                                is_emp = False  # can't detect doctor from diarization
                                if text:
                                    print(f"[FINAL][DIAR] {speaker_label}: {text}", flush=True)
                                    asyncio.create_task(send_chat_webhook(
                                        ctx, room_name, topic_id,
                                        sender_name, sender_id,
                                        receive_name, receive_id,
                                        is_emp, text
                                    ))
                            else:
                                # non-diarization / normal flow: use the participant identity (speaker param)
                                alt_text = text or ""
                                if alt_text:
                                    speaker_clean = clean_participant_name(speaker)
                                    # Determine partner
                                    other_identity = get_other_participant_identity(ctx, speaker)
                                    other_clean = clean_participant_name(other_identity) if other_identity else None

                                    # Try to reuse original logic for ids/names
                                    speaker_is_bs = is_doctor_identity(speaker_clean)
                                    other_is_bs = is_doctor_identity(other_clean) if other_clean else False

                                    sender_id = CUSTOMER_ID
                                    receive_id = OTHER_CUSTOMER_ID
                                    sender_name = speaker_clean
                                    receive_name = other_clean or "Unknown"

                                    # Determine ids/names based on rules (same as before)
                                    if speaker_is_bs:
                                        # speaker is doctor
                                        parts = room_name.split("_") if room_name else []
                                        doctor_id = parts[1] if len(parts) > 1 else None
                                        if doctor_id:
                                            sender_id = doctor_id
                                        receive_id = CUSTOMER_ID
                                        receive_name = other_clean or "Customer"
                                    elif other_is_bs:
                                        parts = room_name.split("_") if room_name else []
                                        doctor_id = parts[1] if len(parts) > 1 else None
                                        sender_id = CUSTOMER_ID
                                        receive_id = doctor_id if doctor_id else CUSTOMER_ID
                                        receive_name = other_clean or "Bs"
                                    else:
                                        # none is BS -> deterministic mapping
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
                                        if not identities:
                                            if other_clean:
                                                identities = [speaker_clean, other_clean]
                                            else:
                                                identities = [speaker_clean]

                                        if identities and identities[0] == speaker_clean:
                                            sender_id = CUSTOMER_ID
                                            receive_id = OTHER_CUSTOMER_ID
                                            receive_name = other_clean or "Other"
                                        else:
                                            sender_id = OTHER_CUSTOMER_ID
                                            receive_id = CUSTOMER_ID
                                            receive_name = other_clean or "Other"

                                    is_emp = speaker_is_bs
                                    print(f"[FINAL] {speaker_clean}: {alt_text}", flush=True)
                                    asyncio.create_task(send_chat_webhook(
                                        ctx, room_name, topic_id,
                                        sender_name, sender_id,
                                        receive_name, receive_id,
                                        is_emp, alt_text
                                    ))
                    elif event.type == SpeechEventType.INTERIM_TRANSCRIPT:
                        # log interim (kept behavior)
                        for alt in alts:
                            text = ""
                            if hasattr(alt, "text"):
                                text = getattr(alt, "text") or ""
                            elif isinstance(alt, dict):
                                text = alt.get("text", "") or ""
                            if text:
                                print(f"[INTERIM] {speaker}: {text}", flush=True)
                    await asyncio.sleep(0)
            except Exception as e:
                print(f"[ERROR] pump_stt crashed for {speaker}: {e}", flush=True)
            finally:
                print(f"[STT END] {speaker} stt_stream finished", flush=True)

        # chạy song song audio + stt
        asyncio.create_task(pump_audio())
        asyncio.create_task(pump_stt())

    print("✅ Agent started, waiting for audio...", flush=True)
    while True:
        await asyncio.sleep(1)

# ---------------------------
# Entrypoint runner
# ---------------------------
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
