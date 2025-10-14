import os
from livekit.agents import AgentSession
from livekit.plugins import google, silero#, elevenlabs, openai
from livekit.agents.llm.mcp import MCPServerHTTP
from datetime import timedelta
import httpx

from livekit.plugins.turn_detector.multilingual import MultilingualModel

async def build_session():
    # MCP server
    mcp = MCPServerHTTP(
        url="http://localhost:9003/sse",
        timeout=60.0,                # thay vì mặc định 5s
        sse_read_timeout=60.0,     # đọc SSE tối đa 1h
        client_session_timeout_seconds=60.0  # client session timeout 60s
    )

    await mcp.initialize()
# sau khi khởi tạo mcp
    # tăng timeout toàn cục cho MCP session


    session = AgentSession(
    # Turn detection bằng mô hình đa ngôn ngữ
    turn_detection=MultilingualModel(),

    # STT Google
    stt=google.STT(
        languages=["vi-VN"],
        model="latest_long",
        punctuate=True,
        interim_results=True,
        credentials_file="/root/AGENT/voicebot-booking2/google_key.json",
    ),

    # LLM Google
    llm=google.LLM(
        model="gemini-2.5-flash",
        api_key=os.getenv("GOOGLE_API_KEY"),
    ),

    # TTS Google
    #tts = elevenlabs.TTS(
       # api_key=os.getenv("ELEVENLABS_API_KEY"),
        #voice_id="EXAVITQu4vr4xnSDxMaL",  # giọng nữ
    #),

        # TTS OpenAI
    #tts=openai.TTS(
       # model="tts-1-hd",
        #voice="nova"
       # ),    
    tts=google.TTS(
       voice_name="en-US-Chirp3-HD-Leda",
       credentials_file="/root/AGENT/voicebot-booking2/google_key.json",
       speaking_rate=1.15,
   ),
    

    # VAD Silero
    vad=silero.VAD.load(
        min_silence_duration=0.35,
        min_speech_duration=0.12,
        activation_threshold=0.40,
    ),

    mcp_servers=[mcp],
    allow_interruptions=True,
    preemptive_generation=True,
    discard_audio_if_uninterruptible=False,
)

    return session
