# ... (các import y như bạn)
import os
import threading
import uvicorn
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI

from livekit.agents import JobContext, WorkerOptions, cli

from app.agent import AssistantAgent
from app.agent_session import build_session

load_dotenv()
app = FastAPI()
global_agent_session = None

@app.get("/ai-agent/health")
async def health_check():
    return {
        "status": "healthy" if global_agent_session else "not ready",
        "timestamp": datetime.utcnow().isoformat()
    }

def start_health():
    uvicorn.run(app, host="0.0.0.0", port=8883, log_level="warning", access_log=False)

async def main(ctx: JobContext):
    global global_agent_session
    await ctx.connect(auto_subscribe="audio_only")

    session = await build_session()
    global_agent_session = session

    agent = AssistantAgent("abc.txt")

    # đảm bảo thư mục logs tồn tại
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    await session.start(agent=agent, room=ctx.room)

    async def save_log_on_shutdown(reason=""):
        try:
            filename = os.path.join(log_dir, f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(session.history.to_dict(), f, indent=2, ensure_ascii=False)
            print(f"✅ [shutdown] session kết thúc ({reason}), transcript lưu vào {filename}")
        except Exception as e:
            print(f"[ERROR] Lỗi khi lưu transcript: {e}")

    ctx.add_shutdown_callback(save_log_on_shutdown)

if __name__ == "__main__":
    threading.Thread(target=start_health, daemon=True).start()

    print("🔍 LIVEKIT_URL =", os.environ.get("LIVEKIT_URL"))
    print("🔍 LIVEKIT_API_KEY =", os.environ.get("LIVEKIT_API_KEY"))
    print("🔍 LIVEKIT_API_SECRET =", os.environ.get("LIVEKIT_API_SECRET"))

    cli.run_app(WorkerOptions(entrypoint_fnc=main))
