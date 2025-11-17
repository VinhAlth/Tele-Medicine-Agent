import asyncio
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType
from typing import AsyncIterable
from livekit.plugins import deepgram

load_dotenv()

async def entrypoint(ctx: agents.JobContext):

    # Khi một track mới được subscribe
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        print(f"[TRACK] Subscribed to {participant.identity}")
        asyncio.create_task(process_track(track, participant.identity))

    async def process_track(track: rtc.RemoteTrack, speaker: str):
        """Xử lý audio track của từng người, gửi tới Deepgram STT"""
        stt = deepgram.STT(model="nova-2", language="vi")
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        async def process_stt_stream():
            try:
                async for event in stt_stream:
                    if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                        text = event.alternatives[0].text.strip()
                        if text:
                            print(f"[FINAL] {speaker}: {text}")
                    elif event.type == SpeechEventType.INTERIM_TRANSCRIPT:
                        text = event.alternatives[0].text.strip()
                        # Có thể in interim nếu muốn
                        # print(f"[INTERIM] {speaker}: {text}")
            except Exception as e:
                print(f"⚠️ STT stream error for {speaker}: {e}")

        # Task chạy STT riêng biệt
        stt_task = asyncio.create_task(process_stt_stream())

        try:
            # Luồng audio chạy liên tục
            async for audio_event in audio_stream:
                stt_stream.push_frame(audio_event.frame)
        except Exception as e:
            print(f"⚠️ Audio stream error for {speaker}: {e}")
        finally:
            # Khi track kết thúc, mới end STT stream
            stt_stream.end_input()
            await stt_task
            print(f"[TRACK] Finished processing {speaker}")

    # Kết nối agent tới room
    await ctx.connect()
    print("✅ Agent started, waiting for audio...")

    # Giữ agent chạy liên tục
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
