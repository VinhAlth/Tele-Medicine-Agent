import asyncio
import re
import json
from datetime import datetime
from typing import AsyncIterable, Optional
from dotenv import load_dotenv
import os
import aiohttp
import redis
from livekit.api import LiveKitAPI, ListParticipantsRequest
from livekit.agents import WorkerOptions, JobRequest
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
async def get_current_participants(room_name: str) -> list:
    """Trả về danh sách participant thực tế trong room"""
    try:
        async with LiveKitAPI() as lkapi:
            resp = await lkapi.room.list_participants(ListParticipantsRequest(room=room_name))
            participants = resp.participants  # list ParticipantInfo
            print(f"[INFO] Room {room_name} has {len(participants)} participant(s)")
            return participants
    except Exception as e:
        print(f"[ERROR] Failed to list participants for room={room_name}: {e}")
        return []

# --- Redis / GraphQL helpers ---
def get_topic_id_by_room(room_name: str) -> Optional[str]:
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


def get_room_data(room_name: str) -> dict:
    """Trả về dict chứa topicId và prescriptionId"""
    if not room_name:
        return {}
    try:
        value_json = r.hget("room:online", room_name)
        if not value_json:
            print(f"[WARN] Không tìm thấy room {room_name} trong Redis", flush=True)
            return {}
        data = json.loads(value_json)
        return data
    except Exception as e:
        print(f"[ERROR] Lỗi lấy room data từ Redis room={room_name}: {e}", flush=True)
        return {}
# --- bổ sung hàm đóng topic ---
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

# --- Fill medical form tool ---
async def fill_medical_form(prescriptionId: str, chiefComplaint: str, medicalHistory: str, symptoms: str) -> dict:
    url = f"https://api-gateway.dev.longvan.vn/clinic-service/callback/encounter-session/{prescriptionId}"
    payload = {
        "medicalHistory": medicalHistory,
        "chiefComplaint": chiefComplaint,
        "height": None,
        "weight": None,
        "temperature": None,
        "symptoms": symptoms,
        "allergyDetail": None,
        "otherRiskDetail": None
    }
    async with aiohttp.ClientSession() as session:
        async with session.put(url, json=payload) as resp:
            resp_data = await resp.text()
            print(f"✅ PUT {url} | status: {resp.status} | response: {resp_data}", flush=True)
            return {"status": resp.status, "response": resp_data}

# --- Main Entrypoint ---
async def entrypoint(ctx: agents.JobContext):
    speaker_map: dict[str, str] = {}
    observed_speakers: list[str] = []
    transcript: dict[str, list[dict]] = {}
    topic_id: Optional[str] = None
    doctor_id: Optional[str] = None
    current_participants = 0  # count participants

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
        nonlocal current_participants
        current_participants += 1
        sid = getattr(publication.track, "sid", "unknown") if publication else "unknown"
        print(f"[TRACK] Subscribed to {participant.identity} ({sid})")
        asyncio.create_task(process_track(track, participant.identity))

    # --- Khi connect, lấy topicId và assign doctor ---
    await ctx.connect()
    room_name = ctx.room.name
    topic_id = "691a96973c870874bd64cdd8"  # get_topic_id_by_room(room_name)
    if topic_id:
        doctor_id = await assign_topic_to_doctor(room_name, topic_id)
    print("✅ Agent started, waiting for audio...")
    participants = await get_current_participants(room_name)
    current_participants = len(participants)
    print(f"✅ Agent started, waiting for audio... current_participants={current_participants}")
    # --- callback participant rời ---
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        nonlocal current_participants
        current_participants = max(0, current_participants - 1)
        print(f"[DISCONNECT] {participant.identity}, total={current_participants}", flush=True)

        async def process_llm_summary():
            # Gom toàn bộ final transcript
            lines = []
            for speaker_id, items in transcript.items():
                for item in items:
                    if item["type"] == "final":
                        time_str = datetime.fromisoformat(item["time"]).strftime("%H:%M:%S")
                        lines.append(f"{speaker_id}: {item['text']} | {time_str}")
            lines.sort(key=lambda x: x.split("|")[1].strip())
            conversation_text = "\n".join(lines)

            # --- gọi OpenAI ---
            from openai import AsyncClient
            client = AsyncClient(api_key=os.getenv("OPENAI_API_KEY"))
            prompt = f"""
Bạn là chuyên gia y khoa. Đọc toàn bộ hội thoại dưới đây và trích xuất:
1. chiefComplaint: Lý do khám
2. medicalHistory: Quá trình bệnh lý, triệu chứng hiện tại, thời gian khởi phát
3. symptoms: triệu chứng lâm sàng, chuẩn đoán tóm gọn, chuẩn y khoa

Nếu gặp đúng 1 câu xin chào: thì giả định giả trường hợp, khách ko bình thường
Hội thoại:
{conversation_text}

Trả về JSON với các field: chiefComplaint, medicalHistory, symptoms, các trường này chỉ mô tả bằng text bằng lời nói tự nhiên
"""
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            import json
            resp_text = resp.choices[0].message.content.strip()

            # loại bỏ ```json ... ``` nếu có
            if resp_text.startswith("```"):
                resp_text = resp_text.split("\n", 1)[1]  # bỏ dòng ```json
                resp_text = resp_text.rsplit("```", 1)[0]  # bỏ dòng ``` cuối cùng
                resp_text = resp_text.strip()

            try:
                summary = json.loads(resp_text)
            except json.JSONDecodeError as e:
                print(f"❌ Lỗi parse OpenAI response: {e}")
                print(f"Raw response:\n{resp_text}")
                return


            print(f"✅ OpenAI summary: {summary}", flush=True)

            # --- lấy prescriptionId từ Redis ---
            room_data = get_room_data(room_name)
            prescriptionId = room_data.get("prescriptionId")
            if not prescriptionId:
                print(f"[WARN] Không tìm thấy prescriptionId cho room {ctx.room.name}, bỏ qua fill_medical_form")
                return

            # --- gọi API điền phiếu khám ---
            await fill_medical_form(
                prescriptionId=prescriptionId,
                chiefComplaint=summary.get("chiefComplaint", ""),
                medicalHistory=summary.get("medicalHistory", ""),
                symptoms=summary.get("symptoms", "")
            )

        asyncio.create_task(process_llm_summary())

        if current_participants == 0 and topic_id:
            print(f"[INFO] Room empty, closing topic {topic_id}")
            asyncio.create_task(close_topic(topic_id))

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
        print(f"✅ Transcript saved to {filename}", flush=True)

    ctx.add_shutdown_callback(on_shutdown)

    while True:
        await asyncio.sleep(1)

async def request_fnc(req: JobRequest) -> None:
    await req.accept(
        name="Trợ lý khám bệnh",
        identity="record",
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, request_fnc=request_fnc, agent_name="record"))


# async def request_fnc(req: JobRequest) -> None:
#     await req.accept(
#         name="Trợ lý khám bệnh",
#         identity="record_agent",
#     )


# if __name__ == "__main__":
#     worker_permissions = agents.WorkerPermissions(
#         can_publish=False,
#         can_subscribe=True,
#         can_publish_data=True,
#         #hidden=True
#     )

#     agents.cli.run_app(
#         WorkerOptions(
#             entrypoint_fnc=entrypoint,
#             request_fnc=request_fnc,
#             agent_name="record_agent",