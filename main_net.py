import os
import asyncio
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import google, silero, noise_cancellation

# ===== Load env =====
load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ===== Define your assistant agent =====
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful voice AI assistant.")


# ===== Async callback handler fix =====
async def handle_user_transcript(event):
    print(f"[USER TRANSCRIPT] {event.user_transcript}")
    # example: generate reply after receiving transcript
    await event.session.generate_reply(
        instructions=f"Answer the user: {event.user_transcript}"
    )


def handle_user_transcript_sync(event):
    # sync wrapper for .on()
    asyncio.create_task(handle_user_transcript(event))


# ===== Entrypoint for LiveKit job =====
async def entrypoint(ctx: agents.JobContext):
    # connect to room first
    await ctx.connect()

    # create agent
    assistant_agent = Assistant()

    # setup session
    session = AgentSession(
        turn_detection="stt",
        stt=google.STT(
            languages="vi-VN",
            model="latest_long",
            punctuate=True,
            interim_results=True,
        ),
        llm=google.LLM(
            model="gemini-2.0-flash-001",
            api_key=GOOGLE_API_KEY,
        ),
        tts=google.TTS(
            voice_name="en-US-Chirp3-HD-Leda",
            credentials_file="/home/redknight/voice-ai-bot/google_key.json",
            speaking_rate=1.15,
        ),
        vad=silero.VAD.load(),
    )

    # register transcript callback (sync wrapper)
    session.on("user_transcript", handle_user_transcript_sync)

    # start session with agent
    await session.start(
        assistant_agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC()
        ),
    )

    # optional: greet user
    await session.generate_reply(
        instructions="Greet the user and offer your assistance."
    )

    # proper shutdown
    await ctx.shutdown()


# ===== Run as worker =====
if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
