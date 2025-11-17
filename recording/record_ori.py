import asyncio
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from typing import AsyncIterable
from livekit.plugins import deepgram

load_dotenv()

async def entrypoint(ctx: agents.JobContext):
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        print(f"[TRACK] Subscribed to {participant.identity}")
        asyncio.create_task(process_track(track, participant.identity))

    async def process_track(track: rtc.RemoteTrack, speaker: str):
        stt = deepgram.STT(
            model="nova-2",
            language="vi",  # hoặc "multi" nếu bạn muốn auto detect
        )
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        async def process_stt_stream(stream: AsyncIterable[SpeechEvent]):
            async for event in stream:
                if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                    text = event.alternatives[0].text.strip()
                    if text:
                        print(f"[FINAL] {speaker}: {text}")
                elif event.type == SpeechEventType.INTERIM_TRANSCRIPT:
                    text = event.alternatives[0].text.strip()
                    # if text:
                    #     print(f"[INTERIM] {speaker}: {text}")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(process_stt_stream(stt_stream))
            async for audio_event in audio_stream:
                stt_stream.push_frame(audio_event.frame)
            stt_stream.end_input()

    await ctx.connect()
    print("✅ Agent started, waiting for audio...")
    while True:
        await asyncio.sleep(1)
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))