
# (giữ nguyên imports ban đầu)
import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import aiohttp
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
from livekit.plugins import google, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents import RoomInputOptions
from livekit.rtc import DataPacket
from livekit.agents.llm.mcp import MCPServerHTTP
from datetime import timedelta
import httpx

from livekit.plugins.turn_detector.multilingual import MultilingualModel


# sau khi khởi tạo mcp
    # tăng timeout toàn cục cho MCP session

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Thư mục lưu lịch sử
HISTORY_DIR = "history"
os.makedirs(HISTORY_DIR, exist_ok=True)

WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
BOT_ID = "68aedccde472aa8afe432664"

async def send_message_to_webhook(message: dict):
    """Gửi 1 message tới webhook, đồng thời in log dễ đọc."""
    sender = message.get("senderName", "unknown")
    content = (message.get("content") or "").strip().replace("\n", " ")

    # Lấy meta.source nếu có
    source = message.get("meta", {}).get("node", message.get("meta", {}).get("source", ""))

    prefix = "🧍 USER" if sender.lower() in ["user", "khách", "bệnh nhân"] else "🤖 BOT"
    src_str = f" [{source}]" if source else ""

    print(f"{prefix}{src_str} ➜ {content[:100]}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=message) as resp:
                if 200 <= resp.status < 300:
                    pass  # Không in lại lần nữa cho khỏi rối
                else:
                    text = await resp.text()
                    print(f"[ERROR] Webhook {resp.status}: {text}")
        except Exception as e:
            print(f"[ERROR] Exception sending webhook: {e}")

# -------------------------
# Agent voice (mở rộng)
# -------------------------
class TeleAgent(Agent):
    def __init__(self, room_name: str):
        super().__init__(
            instructions=f"""
Bạn là một trợ lý ảo của phòng khám tiếp khách trong lúc khám online, nhiệm vụ là chiều video giới thiệu về phòng khám cho khách xem trước khi bác sĩ vào.

hãy nói: Chào anh, chị, cảm ơn anh, chị đã đến phòng khám. Trong lúc đợi bác sĩ vào, anh/chị có thể xem video giới thiệu về phòng khám của chúng tôi để hiểu thêm về các dịch vụ và quy trình chăm sóc, hy vọng sẽ giúp anh, chị cảm thấy thoải mái và yên tâm hơn" , sau đó gọi tool 'reate_ingress_and_push'

hiện đang ở phòng: {room_name} ( thông tin này chỉ dùng để gọi tool, không tiết lộ)

Yêu cầu: nếu khách có hỏi thì trả lời ngắn gọn, thân thiện, xưng anh hoặc chị, ngoan ngoãn, lễ phép. 
""")
        self.room_name = room_name  # lưu biến cho các handler sau


    async def on_enter(self):
        print("✅ on_enter() được gọi!")
        await asyncio.sleep(0.5)
        greeting_obj = await self.session.generate_reply(
            instructions="Xin chào! Tôi là trợ lý ảo của phòng khám."
        )
        await greeting_obj

    # -------------------------
    # Override transcription_node để bắt TTS-aligned / timed transcript
    # text: AsyncIterable[str|TimedString]
    # model_settings: ModelSettings
    # -------------------------
    async def transcription_node(self, text, model_settings):
        """
        Intercepts agent transcription segments (may be TimedString objects).
        Gửi realtime từng đoạn về webhook và tiếp tục forward về pipeline.
        """
        # Default behaviour is to forward unchanged — chúng ta sẽ forward sau khi emit
        async for seg in text:
            try:
                # seg có thể là str hoặc TimedString object (experimental)
                if isinstance(seg, str):
                    txt = seg
                    timed = None
                else:
                    # TimedString có thể có .text, .words (word-level timings) hoặc start_time/end_time
                    txt = getattr(seg, "text", str(seg))
                    timed = {
                        "start_time": getattr(seg, "start_time", None),
                        "end_time": getattr(seg, "end_time", None),
                        # words nếu có (provider hỗ trợ)
                        "words": getattr(seg, "words", None),
                    }

                # chuẩn hoá payload gửi realtime (role ở đây là 'assistant' vì transcription_node thường áp dụng cho output)
                webhook_msg = {
                    "senderName": "assistant",
                    "senderId": "Bot",
                    "receiveId": "09029292222",
                    "receiveName": "user",
                    "role": True,
                    "type": "text",
                    "content": txt,
                    "timestamp": datetime.now().isoformat(),
                    "meta": {
                        "timed": timed,
                        "node": "transcription_node",
                    },
                    "botId": BOT_ID,
                    "status": 1
                }
                #Gửi không block pipeline lâu — nhưng đợi 1 lần để tránh overflow
                #await send_message_to_webhook(webhook_msg)
            except Exception as e:
                print(f"[WARN] transcription_node handler error: {e}")

            # Forward segment tới pipeline (yield lại)
            yield seg

# -------------------------
# Entry point Worker
# -------------------------
ALLOWED_ROOMS = [f"PhongCheck{i:02}" for i in range(1, 11)]  # PhongKham01 -> PhongKham10

def _kind_to_str(kind):
    """Normalize track kind to lowercase string."""
    try:
        return kind.name.lower()
    except Exception:
        return str(kind).lower()


async def participant_entrypoint(ctx: JobRequest, participant: "RemoteParticipant"):
    """Check and log participant's current mic/camera status."""
    identity = getattr(participant, "identity", None) or getattr(participant, "sid", "<unknown>")
    print(f"[JOIN] Participant detected: {identity}")

    try:
        pubs = list(participant.tracks.values()) if getattr(participant, "tracks", None) else []
    except Exception:
        pubs = []

    camera_on = False
    mic_on = False

    for pub in pubs:
        kind_str = _kind_to_str(getattr(pub, "kind", ""))
        is_muted = bool(getattr(pub, "muted", False))

        if "video" in kind_str or "camera" in kind_str:
            camera_on = camera_on or (not is_muted)
        if "audio" in kind_str:
            mic_on = mic_on or (not is_muted)

    cam_status = "ON" if camera_on else "OFF"
    mic_status = "ON" if mic_on else "OFF"
    print(f"[STATUS] {identity} → CAMERA: {cam_status} | MIC: {mic_status}")


async def entrypoint(ctx: JobRequest):
        # MCP server
    # mcp = MCPServerHTTP(
    #     url="http://0.0.0.0:9004/sse",
    #     timeout=60.0,                # thay vì mặc định 5s
    #     sse_read_timeout=60.0,     # đọc SSE tối đa 1h
    #     client_session_timeout_seconds=60.0  # client session timeout 60s
    # )
    #await mcp.initialize()

    if ctx.room.name in ALLOWED_ROOMS:
        print(f"[INFO] Room '{ctx.room.name}' không được phép, agent sẽ không join.")
        return

    session = AgentSession(
        turn_detection=MultilingualModel(),
        # stt=google.STT(
        #     languages=["vi-VN"],
        #     model="latest_long",
        #     punctuate=True,
        #     interim_results=True,
        #     credentials_file=GOOGLE_CREDENTIALS_FILE,
        # ),
        stt= openai.STT(
            model="gpt-4o-mini-transcribe",  # Hoặc "whisper-1" nếu muốn
            language="vi",                  # Tiếng Việt
        ),
        llm=google.LLM(
            model="gemini-2.0-flash",
            api_key=GOOGLE_API_KEY,
        ),
        # tts=google.TTS(
        #     voice_name="en-US-Chirp3-HD-Leda",
        #     credentials_file=GOOGLE_CREDENTIALS_FILE,
        #     speaking_rate=1.25,
        # ) 
        
        tts=openai.TTS(
         model="tts-1-hd",
        voice="nova"
       ),
       #mcp_servers=[mcp]
    )

    agent = TeleAgent(room_name=ctx.room.name)
    #await session.start(room=ctx.room, agent=agent)


    # ------------------------->
    # Handle user-typed chat messages
    # ------------------------->
    def on_data_received_sync(packet: DataPacket):
        # Ignore messages sent by the agent itself to prevent a feedback loop
        if packet.participant and packet.participant.identity == ctx.room.local_participant.identity:
            return

        async def process_data():
            if packet.topic == 'lk-chat-topic':
                try:
                    payload = json.loads(packet.data.decode('utf-8'))
                    message = payload.get('message', '')
                    if message:
                                            print(f"[INFO] Received typed chat from user: {message}")

                                            # Directly build and send webhook for typed chat
                                            webhook_payload = {
                                                "senderName": "user",
                                                "senderId": packet.participant.identity if packet.participant else "unknown",
                                                "receiveId": "Bot",
                                                "receiveName": "Bot",
                                                "role": False,
                                                "type": "text",
                                                "content": message,
                                                "timestamp": datetime.now().isoformat(),
                                                "meta": { "source": "chat", "is_final": True },
                                                "botId": BOT_ID,
                                                "status": 1
                                            }
                                            await send_message_to_webhook(webhook_payload)

                except json.JSONDecodeError:
                    print(f"[WARN] Could not decode chat message: {packet.data}")

        asyncio.create_task(process_data())

    ctx.room.on("data_received", on_data_received_sync)

    # -------------------------
    # Realtime event handlers: user input & conversation items
    # -------------------------
    from livekit.agents import UserInputTranscribedEvent, ConversationItemAddedEvent

    @session.on("conversation_item_added")
    def _on_conversation_item_added(event: ConversationItemAddedEvent):
        async def handler():
            try:
                item = event.item
                # 🧠 Bỏ qua event nếu chưa final
                if getattr(item, "is_final", True) is False:
                    return

                role = getattr(item, "role", "assistant")
                text_parts = []
                for content in getattr(item, "content", []):
                    if isinstance(content, str):
                        text_parts.append(content)
                    else:
                        text_parts.append(getattr(content, "text", getattr(content, "transcript", str(content))))
                combined = "\n".join(p for p in text_parts if p).strip()
                if not combined:
                    return

                payload = {
                    "senderName": role,
                    "senderId": "Bot" if role != "user" else getattr(item, "speaker_id", "user"),
                    "receiveId": "Bot" if role == "user" else "09029292222",
                    "receiveName": "Bot" if role == "user" else "user",
                    "role": True if role != "user" else False,
                    "type": "text",
                    "content": combined,
                    "timestamp": datetime.now().isoformat(),
                    "meta": {
                        "interrupted": getattr(item, "interrupted", False),
                    },
                    "botId": BOT_ID,
                    "status": 1
                }
                if role != "user":
                    await send_message_to_webhook(payload)
                    chat_payload = json.dumps({"message": combined})
                    await ctx.room.local_participant.publish_data(
                        payload=chat_payload.encode('utf-8'),
                        topic='lk-chat-topic'
                    )
            except Exception as e:
                print(f"[WARN] on_conversation_item_added handler error: {e}")

        asyncio.create_task(handler())

    # Start session & attach agent
    await session.start(room=ctx.room, agent=agent)

    await ctx.connect()

    # 🔹 1️⃣ Call manually for participants already in the room
    try:
        existing = list(ctx.room.remote_participants.values())
    except Exception:
        existing = []
    if existing:
        print(f"🔍 Found {len(existing)} participant(s) already in the room, checking initial states...")
        for p in existing:
            await participant_entrypoint(ctx, p)
    else:
        print("No existing participants found (waiting for new joins).")

    # 🔹 2️⃣ Register callback for new user joins
    ctx.add_participant_entrypoint(participant_entrypoint)

    # 🔹 3️⃣ Event handlers for real-time mic & cam toggles
    @ctx.room.on("track_muted")
    def _on_track_muted(participant, publication):
        p_id = getattr(participant, "identity", getattr(participant, "sid", "<unknown>"))
        kind = _kind_to_str(getattr(publication, "kind", ""))
        label = "CAMERA" if "video" in kind else "MIC" if "audio" in kind else kind.upper()
        print(f"[UPDATE] {p_id} → {label}: OFF")

    @ctx.room.on("track_unmuted")
    def _on_track_unmuted(participant, publication):
        p_id = getattr(participant, "identity", getattr(participant, "sid", "<unknown>"))
        kind = _kind_to_str(getattr(publication, "kind", ""))
        label = "CAMERA" if "video" in kind else "MIC" if "audio" in kind else kind.upper()
        print(f"[UPDATE] {p_id} → {label}: ON")

# -------------------------
# Request function để filter job
# -------------------------
async def request_fnc(req: JobRequest) -> None:
    # Luôn chấp nhận request và đặt thông tin agent
    await req.accept(
        name="Trợ lý khám bệnh",     # tên hiển thị trong room
        identity="assistant_agent",   # định danh agent
        # attributes={"role": "assistant"}  # tuỳ chọn thêm nếu cần
    )
# -------------------------
# Chạy Worker
# -------------------------
if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=request_fnc,  # Filter phòng ở đây
        agent_name="assistant_agent"  # Phân biệt worker
    ))
