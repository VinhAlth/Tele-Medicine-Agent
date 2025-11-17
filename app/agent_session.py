import os
from livekit.agents import AgentSession
from livekit.plugins import google, silero, openai, assemblyai#, elevenlabs
from livekit.agents.llm.mcp import MCPServerHTTP
from datetime import timedelta
import httpx

from livekit.plugins.turn_detector.multilingual import MultilingualModel

async def build_session():
    # MCP server
    mcp = MCPServerHTTP(
        url="http://45.119.86.209:9002/sse",
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

    #STT Google
    stt= openai.STT(
        model="gpt-4o-transcribe",  # Hoặc "whisper-1" nếu muốn
        language="vi",         
        prompt="Bạn hãy lắng nghe tiếng việt, và ghi nhận tiếng việt thật chuẩn xác",         # Tiếng Việt
    ),
    # stt = assemblyai.STT(
    #   end_of_turn_confidence_threshold=0.4,
    #   min_end_of_turn_silence_when_confident=400,
    #   max_turn_silence=1280,
    # ),
    
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
    tts=openai.TTS(
       model="tts-1-hd",
       voice="nova"
       ),    
    
    #tts=google.TTS(
      # voice_name="en-US-Chirp3-HD-Leda",
      # credentials_file="/root/AGENT/voicebot-booking2/google_key.json",
      # speaking_rate=1.15,
   #),
     

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
