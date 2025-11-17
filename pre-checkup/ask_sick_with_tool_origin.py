import os
import json
import asyncio
from dotenv import load_dotenv
from datetime import datetime

import aiohttp
import redis
from livekit.agents import AgentSession, inference
from livekit.agents import Agent, AgentSession, ConversationItemAddedEvent
from livekit.plugins import openai, silero, assemblyai, deepgram
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents import Agent, AgentSession, ConversationItemAddedEvent, RunContext, function_tool
from dotenv import load_dotenv
#from app.agent_session import build_session


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# -------------------------
# Configuration / Constants
# -------------------------
WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
GRAPHQL_URL = "https://crm-ticket-gateway.dev.longvan.vn/crm-graph-gateway/graphql"
BOT_ID = "68aedccde472aa8afe432664"

# Fallback / fixed IDs as requested:
FIXED_TOPIC_ID = "6911a8d7032d2d156646f0ce"        # fallback topic id if Redis lookup fails
EMPLOYEE_SENDER_ID = "123"             # cố định cho 1 ông (employee)
PATIENT_SENDER_ID = "456"              # cố định cho 1 ngược lại (patient)
EMPLOYEE_RECEIVE_ID = EMPLOYEE_SENDER_ID
PATIENT_RECEIVE_ID = PATIENT_SENDER_ID
WEBHOOK_URL = "https://com-hub.dev.longvan.vn/com-hub/v1/web-hook/sendMessage/AICHAT"
# Redis client (synchronous redis-py client)
r = redis.Redis(
    host='redis-connect.dev.longvan.vn',
    port=32276,
    password='111111aA',
    decode_responses=True
)


# -------------------------
# Redis helper
# -------------------------
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


# -------------------------
# GraphQL assign topic -> doctor
# -------------------------
async def assign_topic_to_doctor(room_name: str, topic_id: str):
    """
    Gán assignee (doctor) cho topic. DoctorId lấy từ room_name theo pattern <something>_<doctorId>...
    Nếu không tìm được doctorId, sẽ in ra lỗi.
    """
    if not topic_id:
        print(f"[SKIP] Không có topicId cho room={room_name}", flush=True)
        return

    try:
        # giả sử room_name dạng: prefix_<doctorId>_...
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


# -------------------------
# Webhook sender
# -------------------------
async def send_message_to_webhook(message: dict):
    """
    Gửi một message dict tới WEBHOOK_URL (POST JSON).
    In log request/response để tiện debug.
    """
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
                    print(f"[WEBHOOK OK] {message.get('senderName')} ➜ {message.get('content')}", flush=True)
                else:
                    print(f"[WEBHOOK FAILED] {message.get('senderName')} ➜ {message.get('content')} | Status: {resp.status}", flush=True)
        except Exception as e:
            try:
                print(f"[WEBHOOK ERROR] {message.get('senderName')} ➜ {message.get('content')} | Error: {e}", flush=True)
            except Exception:
                print(f"[WEBHOOK ERROR] (while logging) Error: {e}", flush=True)


# -------------------------
# Agent class + entrypoint
# -------------------------/root/AGENT/Tele_Medician/pre-checkup/ask_sick_with_tool.py
class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
Bạn là Long Vân, một trợ lý y khoa thân thiện, đang hỗ trợ bệnh nhân điền phiếu khám online. Nhiệm vụ của bạn là hướng dẫn bệnh nhân mô tả tình trạng bệnh một cách nhẹ nhàng, dễ hiểu và chính xác.

Nguyên tắc hội thoại:

Hỏi lần lượt từng câu, tổng cộng 3 bước. Không hỏi dồn nhiều câu cùng lúc.

Sau mỗi câu trả lời, cảm ơn/đồng cảm trước khi chuyển sang câu tiếp theo.

Khi đã thu thập đủ 3 thông tin, gọi tool fill_medical_form để gửi dữ liệu.

Không dùng từ chuyên môn khó hiểu. Chỉ hỏi ngoài 3 câu chính nếu cần câu phụ để khuyến khích bệnh nhân mô tả rõ hơn.

Quy trình hội thoại:

Bước 0 – Hỏi lý do khám (dẫn dắt các câu sau dựa trên lý do này):
Ví dụ:
“Chào anh, chị 👋 Em là Long Vân, trợ lý y Khoa, em sẽ giúp anh, chị điền phiếu khám nhé.
Trước tiên, anh, chị có thể cho em biết lý do hôm nay đi khám là gì ạ? (Ví dụ: cảm thấy mệt mỏi, đau bụng, kiểm tra sức khỏe định kỳ…)”

Bước 1 – Mô tả quá trình bệnh lý:
Dựa trên lý do khám, hỏi nhẹ nhàng để bệnh nhân kể chi tiết:
Ví dụ:
“Cảm ơn anh, chị đã chia sẻ. anh, chị có thể mô tả thêm quá trình bệnh lý gần đây được không?
Ví dụ: bắt đầu từ khi nào, đã điều trị ở đâu, tình trạng có cải thiện hay nặng hơn không…”

Bước 2 – Triệu chứng hiện tại:
Dẫn dắt theo lý do khám và quá trình bệnh lý đã kể:
Ví dụ:
“Dạ em hiểu rồi ạ. Hiện tại thì anh, chị đang gặp những triệu chứng gì cụ thể ạ?
Ví dụ: đau đầu, ho, sốt, mệt mỏi, buồn nôn…”

Bước 3 – Thời gian khởi phát:
Dựa trên triệu chứng hiện tại, hỏi thời gian xuất hiện:
Ví dụ:
“Cho em hỏi thêm, các triệu chứng này bắt đầu xuất hiện từ khi nào vậy ạ?
Ví dụ: hôm qua, cách đây vài ngày, hay đã kéo dài vài tuần rồi…”

Bước 4 – Gửi dữ liệu:
Khi đã có đủ 3 thông tin, gọi fill_medical_form để gửi dữ liệu bệnh nhân lên hệ thống.

Lưu ý:

Giữ giọng điệu thân thiện, nhẹ nhàng, như điều dưỡng tận tâm.

Luôn phản hồi cảm ơn hoặc đồng cảm trước khi chuyển câu kế tiếp.

Hỏi câu phụ chỉ khi bệnh nhân trả lời quá ngắn để khuyến khích mô tả rõ hơn.

Luôn cá nhân hóa theo lý do khám.
                """
        )

    @function_tool()
    async def fill_medical_form(self, context: RunContext, chiefComplaint: str, medicalHistory: str, symptoms: str) -> dict:
        """
        Bạn là chuyên gia y khoa hãy tổng hợp thông tin thu thập được và gọi chức năng này.
        Tool này để điền phiếu khám
        chiefComplaint: Lý do khám
        medicalHistory: Là mô tả chuẩn y khoa của 3 thông tin sau: Mô tả quá trình bệnh lý, Triệu chứng hiện tại, Thời gian khởi phát
        symptoms: triệu chứng lâm sàng, chuẩn đoán tóm gọn, chuẩn y khoa, chuyên môn y tế
        """
        presctiptionId = "20.1387.1449"
        url = f"https://api-gateway.dev.longvan.vn/clinic-service/callback/encounter-session/{presctiptionId}"
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


    async def on_enter(self):
        print("✅ on_enter() được gọi!", flush=True)
        await asyncio.sleep(0.2)
        greeting_obj = await self.session.generate_reply(
            instructions="Chào hỏi bệnh nhân thân thiện và giải thích vai trò"
        )
        await greeting_obj

    async def on_start(self):
        self._setup_event_listeners()
        return await super().on_start()


async def entrypoint(ctx):
    session = AgentSession(
        turn_detection=MultilingualModel(),
        # stt= openai.STT(
        #     model="gpt-4o-transcribe",  # Hoặc "whisper-1" nếu muốn
        #     language="vi",                  # Tiếng Việt
        # ),
        # stt= openai.STT(
        #     model="gpt-4o-transcribe",  # Hoặc "whisper-1" nếu muốn
        #     language="vi",         
        #     prompt=
        #         '''Bạn đang nghe hội thoại giữa bệnh nhân và nhân viên y tế bằng tiếng Việt. 
        #         Hãy nhận diện chính xác từng câu nói bằng tiếng Việt, giữ nguyên cách nói tự nhiên 
        #         (bao gồm từ địa phương, cách nói mộc mạc), và cố gắng ghi lại đầy đủ thông tin liên quan đến bệnh lý, 
        #         triệu chứng, thời gian khởi phát, tiền sử bệnh và lý do đi khám. 
        #         Không dịch, không tóm tắt, chỉ ghi lại lời nói đúng chính tả tiếng Việt, có dấu đầy đủ.'''
        stt = deepgram.STT(
            model="nova-2",
            language="vi",
            interim_results=True,
            sample_rate=16000,
            #encoding="linear16",
            #channels=1
),

    #     stt = assemblyai.STT(
    #         languages="vi",                  
    #         end_of_turn_confidence_threshold=0.4,
    #         min_end_of_turn_silence_when_confident=400,
    #         max_turn_silence=1280,
    # ),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(voice="alloy", model="tts-1"),
        vad=silero.VAD.load(
            min_silence_duration=0.35,
            min_speech_duration=0.12,
            activation_threshold=0.40,
        ),
    )

    # --- xử lý message gửi lên webhook mỗi khi item mới xuất hiện ---
    # we wrap actual async processing in handle_item and schedule with create_task
    async def handle_item(event: ConversationItemAddedEvent):
        """
        Xử lý event bất đồng bộ: build webhook message và gửi.
        Gọi assign_topic_to_doctor nếu có topic_id.
        """
        try:
            # cố gắng lấy text content (tùy implementation của event.item)
            text = getattr(event.item, "text_content", None) or getattr(event.item, "content", None)
            role = getattr(event.item, "role", None)  # "user" hoặc "assistant" ...
            participant = getattr(event, "participant", None) or getattr(event.item, "participant", None)

            # Speaker name: cố gắng lấy participant identity/ name, fallback role
            speaker_name = None
            if participant:
                speaker_name = getattr(participant, "identity", None) or getattr(participant, "name", None)
            if not speaker_name:
                # fallback to role
                speaker_name = role or "unknown"

            # Decide is_employee, sender/receive IDs and names
            is_employee = False
            if role and role.lower() != "user":
                is_employee = True

            # Decide is_employee, sender/receive IDs and names
            if role == "user":
                is_employee = False
                sender_id = PATIENT_SENDER_ID
                receive_id = EMPLOYEE_RECEIVE_ID
                receive_name = "assistant"
            else:
                is_employee = True
                sender_id = EMPLOYEE_SENDER_ID
                receive_id = PATIENT_RECEIVE_ID
                receive_name = "bệnh nhân"


            # timestamp and content
            alt_text = text if text is not None else ""

            # topic id from redis (or fallback)
            try:
                room_name = ctx.room.name
            except Exception:
                room_name = None

            topic_id = None
            if room_name:
                topic_id = get_topic_id_by_room(room_name)
            if not topic_id:
                topic_id = FIXED_TOPIC_ID

            # build webhook message
            webhook_msg = {
                "senderName": speaker_name,
                "senderId": sender_id,
                "receiveId": receive_id,
                "receiveName": receive_name,
                "isMessageFromEmployee": is_employee,
                "type": "text",
                "content": alt_text,
                "timestamp": datetime.now().isoformat(),
                "botId": BOT_ID,
                "topicId": "69130922f18f4b46dc1f6e67",
                "isMessageInGroup": False
            }

            # send webhook (await)
            await send_message_to_webhook(webhook_msg)

            # optionally, if we have a real topic id (not fallback), try assign to doctor in background
            if topic_id and topic_id != FIXED_TOPIC_ID and room_name:
                # run assign in background
                asyncio.create_task(assign_topic_to_doctor(room_name, topic_id))

        except Exception as e:
            print(f"[ERROR] handle_item exception: {e}", flush=True)

    # --- đăng ký event listener: mỗi khi có conversation_item_added, schedule handle_item ---
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        # giữ bản in ở console để debug nhanh
        role = getattr(event.item, "role", None)
        text = getattr(event.item, "text_content", None) or getattr(event.item, "content", None)
        if text:
            if role == "user":
                print(f"[USER] {text}", flush=True)
            else:
                print(f"[ASSISTANT] {text}", flush=True)
        # schedule async handler (non-blocking)
        try:
            asyncio.create_task(handle_item(event))
        except Exception as e:
            print(f"[ERROR] cannot create task for handle_item: {e}", flush=True)

    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)
    await ctx.connect()


if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
