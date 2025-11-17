import os
import re
import asyncio
from dotenv import load_dotenv
from typing import AsyncIterable

from livekit.agents import Agent, AgentSession, ModelSettings, ConversationItemAddedEvent
from livekit.agents.llm import ImageContent, AudioContent
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def _extract_text_from_obj(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if hasattr(obj, "content"):
        try:
            return obj.content or ""
        except Exception:
            pass
    if hasattr(obj, "delta"):
        delta = getattr(obj, "delta")
        if hasattr(delta, "content"):
            try:
                return delta.content or ""
            except Exception:
                pass
        if isinstance(delta, dict):
            return "".join([v for k, v in delta.items() if k == "content" and isinstance(v, str)]) or ""
    if isinstance(obj, dict):
        if "content" in obj and isinstance(obj["content"], str):
            return obj["content"]
        if "delta" in obj and isinstance(obj["delta"], dict) and "content" in obj["delta"]:
            return obj["delta"]["content"] or ""
    s = str(obj)
    matches = re.findall(r"content='([^']*)'", s)
    if matches:
        return "".join(matches)
    return ""


class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
Bạn là một trợ lý y tế ảo đang hỗ trợ bệnh nhân điền phiếu khám online.
(Hãy giữ nguyên kịch bản của bạn)
"""
        )

    async def on_enter(self):
        print("✅ on_enter() được gọi!", flush=True)
        await asyncio.sleep(0.2)
        greeting_obj = await self.session.generate_reply(
            instructions="Chào hỏi bệnh nhân thân thiện và giải thích vai trò"
        )
        await greeting_obj

    async def transcription_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        async for chunk in text:
            txt = _extract_text_from_obj(chunk)
            if txt:
                print(f"[TRANSCRIPTION CHUNK] {txt}", flush=True)
            yield chunk

    async def llm_node(self, chat_ctx, tools, model_settings: ModelSettings) -> AsyncIterable[str]:
        full_text = ""
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            txt = _extract_text_from_obj(chunk)
            if txt:
                full_text += txt
        MAX_TTS_CHARS = 4096
        if len(full_text) > MAX_TTS_CHARS:
            trimmed = full_text[:MAX_TTS_CHARS]
            print(f"[LLM OUTPUT FULL - trimmed] {trimmed}", flush=True)
            yield trimmed
        else:
            print(f"[LLM OUTPUT FULL] {full_text}", flush=True)
            yield full_text

    def _setup_event_listeners(self):
        # conversation_item_added
        def conversation_item_added_cb(event):
            asyncio.create_task(self._handle_conversation_item_added(event))
        self.session.on("conversation_item_added", conversation_item_added_cb)

        # user_input_transcribed
        def user_input_transcribed_cb(event):
            asyncio.create_task(self._handle_user_input_transcribed(event))
        self.session.on("user_input_transcribed", user_input_transcribed_cb)

    async def _handle_conversation_item_added(self, event: ConversationItemAddedEvent):
        try:
            item = event.item
            role = getattr(item, "role", None)
            interrupted = getattr(item, "interrupted", False)
            print(f"[CONVERSATION ITEM ADDED] role={role}, interrupted={interrupted}", flush=True)

            if hasattr(item, "text_content") and item.text_content:
                print(f" - text_content: {item.text_content}", flush=True)

            if hasattr(item, "content") and item.content:
                for c in item.content:
                    if isinstance(c, str):
                        print(f" - text chunk: {c}", flush=True)
                    elif hasattr(c, "transcript"):
                        print(f" - audio chunk transcript: {c.transcript}", flush=True)
                    elif hasattr(c, "image"):
                        print(f" - image chunk: {c.image}", flush=True)
                    else:
                        print(f" - unknown chunk: {_extract_text_from_obj(c)}", flush=True)

            if (not getattr(item, "text_content", None)) and (not getattr(item, "content", None)):
                fallback_text = _extract_text_from_obj(item)
                if fallback_text:
                    print(f" - fallback text: {fallback_text}", flush=True)
        except Exception as e:
            print("[ERROR] in conversation_item_added handler:", e, flush=True)

    async def _handle_user_input_transcribed(self, event):
        try:
            transcript = getattr(event, "transcript", "")
            is_final = getattr(event, "is_final", False)
            lang = getattr(event, "language", None)
            if is_final and transcript:
                print(f"[USER STT FINAL] ({lang}) {transcript}", flush=True)
        except Exception as e:
            print("[ERROR] in user_input_transcribed handler:", e, flush=True)

    async def on_start(self):
        self._setup_event_listeners()
        return await super().on_start()


async def entrypoint(ctx):
    session = AgentSession(
        turn_detection=MultilingualModel(),
        stt=openai.STT(language="en"),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(voice="alloy", model="tts-1"),
        vad=silero.VAD.load(min_silence_duration=0.35, min_speech_duration=0.12, activation_threshold=0.40),
    )

    # --- copy y hệt style docs ---
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        print(f"[DOCS EVENT] role={event.item.role}, text_content={event.item.text_content}")
        for content in event.item.content:
            if isinstance(content, str):
                print(f" - text: {content}")
            elif isinstance(content, ImageContent):
                print(f" - image: {content.image}")
            elif isinstance(content, AudioContent):
                print(f" - audio: {content.frame}, transcript: {content.transcript}")

    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)
    await ctx.connect()


if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
