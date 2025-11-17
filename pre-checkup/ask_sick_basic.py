import os
import asyncio
from dotenv import load_dotenv

from livekit.agents import Agent, AgentSession, ConversationItemAddedEvent
from livekit.plugins import openai, silero, deepgram
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
Bạn là một trợ lý y tế ảo đang hỗ trợ bệnh nhân điền phiếu khám online.
(Hãy giữ nguyên kịch bản của bạn), nói ngắn gọn thoio, ko qua 5 từ
"""
        )

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
        stt = deepgram.STT(
            model="nova-2",
            language="vi",
            interim_results=True,
            sample_rate=16000,
            #encoding="linear16",
            #channels=1
),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(voice="alloy", model="tts-1"),
        vad=silero.VAD.load(
            min_silence_duration=0.35,
            min_speech_duration=0.12,
            activation_threshold=0.40,
        ),
    )

    # --- chỉ in text, phân biệt user/assistant ---
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        role = event.item.role
        text = getattr(event.item, "text_content", None)
        if text:
            if role == "user":
                print(f"[USER] {text}")
            else:
                print(f"[ASSISTANT] {text}")
    from livekit.agents import UserInputTranscribedEvent

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:  # chỉ gửi bản transcript cuối
            print(f"[FINAL USER] {event.transcript}")
            # Xử lý tiếp, ví dụ gửi LLM


    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)
    await ctx.connect()


if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
