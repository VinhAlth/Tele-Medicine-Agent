import os
import json
import asyncio
import threading
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn
import aiohttp

from livekit.agents import JobContext, WorkerOptions, cli
from app.agent import AssistantAgent
from app.agent_session import build_session

# --- Load biến môi trường ---
load_dotenv()

app = FastAPI()
global_agent_session = None

# --- Webhook config ---
WEBHOOK_URL = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"
BOT_ID = "68aedccde472aa8afe432664"
HISTORY_DIR = "history"
os.makedirs(HISTORY_DIR, exist_ok=True)


# -----------------------------------------------------------
# --- HÀM TIỆN ÍCH ---
# -----------------------------------------------------------

async def send_message_to_webhook(message: dict):
    """Gửi 1 message tới webhook."""
    async with aiohttp.ClientSession() as session:
        async with session.post(WEBHOOK_URL, json=message) as resp:
            if 200 <= resp.status < 300:
                print(f"[INFO] Đã gửi: {message['content'][:60]}...")
            else:
                text = await resp.text()
                print(f"[ERROR] Webhook lỗi: {resp.status} - {text}")

async def send_history_to_webhook(history_dict):
    """Gửi từng message từ history."""
    items = history_dict.get("items", [])
    for item in items:
        role = item.get("role", "user")
        content_list = item.get("content", [])
        content_str = "\n".join(content_list).strip()
        if not content_str:
            continue

        message = {
            "senderName": role,
            "senderId": "09029292222" if role == "user" else "Bot",
            "receiveId": "Bot" if role == "user" else "09029292222",
            "receiveName": "Bot" if role == "user" else "user",
            "role": True,
            "type": "text",
            "content": content_str,
            "timestamp": datetime.now().isoformat(),
            "botId": BOT_ID,
            "status": 1
        }

        await send_message_to_webhook(message)
        await asyncio.sleep(0.2)

def save_call_history(room_name, history_dict):
    """Lưu lịch sử cuộc gọi ra file."""
    file_path = f"{HISTORY_DIR}/{room_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(history_dict, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Đã lưu history: {file_path}")
    return file_path


# -----------------------------------------------------------
# --- FASTAPI HEALTH CHECK ---
# -----------------------------------------------------------
@app.get("/ai-agent/health")
async def health_check():
    return {
        "status": "healthy" if global_agent_session else "not ready",
        "timestamp": datetime.utcnow().isoformat()
    }

def start_health():
    uvicorn.run(app, host="0.0.0.0", port=8883, log_level="warning", access_log=False)


# -----------------------------------------------------------
# --- MAIN AGENT ---
# -----------------------------------------------------------
async def main(ctx: JobContext):
    global global_agent_session
    await ctx.connect(auto_subscribe="audio_only")

    session = await build_session()
    global_agent_session = session

    agent = AssistantAgent("abc.txt")

    await session.start(agent=agent, room=ctx.room)

    # --- Khi job shutdown: lưu và gửi lịch sử ---
    async def save_and_send_history():
        try:
            history_dict = session.history.to_dict()
            save_call_history(ctx.room.name, history_dict)
            await send_history_to_webhook(history_dict)
            print("✅ [shutdown] Đã lưu & gửi toàn bộ lịch sử thành công!")
        except Exception as e:
            print(f"[ERROR] Khi shutdown lưu/gửi history: {e}")

    #ctx.add_shutdown_callback(save_and_send_history, agent_name ="medical_agent")


# -----------------------------------------------------------
# --- KHỞI CHẠY ---
# -----------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=start_health, daemon=True).start()

    print("🔍 LIVEKIT_URL =", os.environ.get("LIVEKIT_URL"))
    print("🔍 LIVEKIT_API_KEY =", os.environ.get("LIVEKIT_API_KEY"))
    print("🔍 LIVEKIT_API_SECRET =", os.environ.get("LIVEKIT_API_SECRET"))

    cli.run_app(WorkerOptions(entrypoint_fnc=main, agent_name="medical_agent"))
    
