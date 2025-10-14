import os
from dotenv import load_dotenv

from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli
from livekit.plugins import assemblyai, google, elevenlabs, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# -------------------------------
# Agent định nghĩa cách phản hồi
# -------------------------------
class VoiceAgent(Agent):
    def __init__(self):
        super().__init__(instructions="Bạn là chuyên viên tư vấn của công ty Long Vân system solution, chuyên tư vấn cho khách hàng về dịch vụ của công ty.")

    async def on_session_started(self, ctx):
        # Khi người dùng vừa kết nối vào room
        await ctx.speak("Xin chào! Tôi có thể giúp gì cho bạn hôm nay?")

# -------------------------------
# Hàm chính tạo agent session
# -------------------------------
async def main(ctx: JobContext):
    # Kết nối tới room từ LiveKit
    await ctx.connect(auto_subscribe="audio_only")

    # Khởi tạo agent session với STT, LLM, TTS và Turn Detection
    session = AgentSession(
        stt=assemblyai.STT(api_key=os.getenv("ASSEMBLYAI_API_KEY")),
        llm=google.LLM(model="gemini-2.0-flash-001", api_key=os.getenv("GOOGLE_API_KEY")),
        tts=elevenlabs.TTS(api_key=os.getenv("ELEVENLABS_API_KEY")),
        vad=silero.VAD.load(),  # voice activity detection
        turn_detection=MultilingualModel()
# phát hiện khi user dừng nói
    )

    # Bắt đầu chạy agent
    await session.start(agent=VoiceAgent(), room=ctx.room)

# -------------------------------
# Khởi động app
# -------------------------------
if __name__ == "__main__":
    load_dotenv()
    cli.run_app(WorkerOptions(entrypoint_fnc=main))
