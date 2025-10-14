import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import aiohttp
from livekit.agents import Agent, AgentSession, WorkerOptions, JobRequest
from livekit.plugins import google
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Thư mục lưu lịch sử
HISTORY_DIR = "history"
os.makedirs(HISTORY_DIR, exist_ok=True)

WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
BOT_ID = "68aedccde472aa8afe432664"

def save_call_history(room_name, history_dict):
    """Lưu lịch sử cuộc gọi vào file JSON."""
    file_path = f"{HISTORY_DIR}/{room_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(history_dict, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Lịch sử cuộc gọi đã lưu: {file_path}")
    return file_path

async def send_message_to_webhook(message: dict):
    """Gửi 1 message tới webhook."""
    async with aiohttp.ClientSession() as session:
        async with session.post(WEBHOOK_URL, json=message) as resp:
            if 200 <= resp.status < 300:
                print(f"[INFO] Sent message: {message['content']}")
            else:
                text = await resp.text()
                print(f"[ERROR] Failed to send message: {resp.status} - {text}")

async def send_history_to_webhook(history_dict):
    """Duyệt history và gửi từng item lần lượt."""
    items = history_dict.get("items", [])
    for item in items:
        role = item.get("role", "user")
        content_list = item.get("content", [])
        content_str = "\n".join(content_list).strip()
        if not content_str:
            continue

        message = {
            "senderName": role,
            "senderId": "09029292222" if role=="user" else "Bot",
            "receiveId": "Bot" if role=="user" else "09029292222",
            "receiveName": "Bot" if role=="user" else "user",
            "role": True,
            "type": "text",
            "content": content_str,
            "timestamp": datetime.now().isoformat(),
            "botId": BOT_ID,
            "status": 1
        }

        await send_message_to_webhook(message)
        await asyncio.sleep(0.2)  # Delay 0.2s giữa các message

# -------------------------
# Agent voice
# -------------------------
class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=''' 
Bạn là một trợ lý ảo của bệnh viện, checking agent, bạn phải hỏi khách hàng, giới  thiệu em là .., trước khi bác sĩ join phòng em có nhiệm vụ kiểm tra và hỗ trợ tình trạng thiết bị như camera và mic cho khách, hỏi khách nhìn camera có rõ, và nghe được bạn nói hay không, nếu nghe nói oke thì cảm ơn và chào tạm biệt, bảo đợi , 1 tí nữa bác sĩ sẽ vào khám bệnh cho bạn!!, nếu không hay hướng dẫn họ kết nối mic và camera trên openvide meetting.'''
        )

    async def on_enter(self):
        print("✅ on_enter() được gọi!")
        await asyncio.sleep(0.5)
        greeting_obj = await self.session.generate_reply(
            instructions="Xin chào! Tôi là trợ lý ảo của phòng khám."
        )
        await greeting_obj

# -------------------------
# Entry point Worker
# -------------------------
ALLOWED_ROOMS = [f"PhongCheck{i:02}" for i in range(1, 11)]  # PhongKham01 -> PhongKham10

async def entrypoint(ctx):
    if ctx.room.name not in ALLOWED_ROOMS:
        print(f"[INFO] Room '{ctx.room.name}' không được phép, agent sẽ không join.")
        return
    session = AgentSession(
        turn_detection=MultilingualModel(),
        stt=google.STT(
            languages=["vi-VN"],
            model="latest_long",
            punctuate=True,
            interim_results=True,
            credentials_file=GOOGLE_CREDENTIALS_FILE,
        ),
        llm=google.LLM(
            model="gemini-2.5-flash",
            api_key=GOOGLE_API_KEY,
        ),
        tts=google.TTS(
            voice_name="en-US-Chirp3-HD-Leda",
            credentials_file=GOOGLE_CREDENTIALS_FILE,
            speaking_rate=1.25,
        ),
    )

    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)

    # --- Callback lưu lịch sử và gửi webhook khi agent tắt / room đóng ---
    async def save_and_send_history():
        history_dict = session.history.to_dict()
        save_call_history(ctx.room.name, history_dict)
        await send_history_to_webhook(history_dict)

    ctx.add_shutdown_callback(save_and_send_history)

    await ctx.connect()

# -------------------------
# Request function để filter job
# -------------------------
async def request_fnc(req: JobRequest) -> None:
    if req.room.name in ALLOWED_ROOMS:
        await req.accept()
    else:
        await req.reject()

# -------------------------
# Chạy Worker
# -------------------------
if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=request_fnc,  # Filter phòng ở đây
        agent_name="check_agent"  # Phân biệt worker
    ))