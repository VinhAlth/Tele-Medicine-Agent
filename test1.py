import os
import asyncio
import aiohttp
from dotenv import load_dotenv

from livekit import agents as lk_agents
from livekit.agents import Agent, JobContext
from livekit import rtc

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_TOKEN = os.getenv("LIVEKIT_TOKEN")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# ---------- Deepgram helper ----------
async def deepgram_stream_audio(pcm_bytes):
    """
    Gửi audio PCM tới Deepgram Realtime STT API.
    """
    url = "wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=48000&language=vi"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            url,
            headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        ) as ws:
            # gửi audio binary
            await ws.send_bytes(pcm_bytes)
            # nhận transcript
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    print("[Deepgram]", msg.data)

# ---------- LiveKit Agent ----------
class MyAgent(Agent):
    async def on_join(self, ctx: JobContext):
        print("[LiveKit] Agent joined room")

        # Subscribe tất cả participant audio track có sẵn
        for participant in ctx.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.kind == rtc.TrackKind.KIND_AUDIO:
                    pub.set_subscribed(True)

        # Lắng nghe track mới được publish
        @ctx.room.on("track_published")
        def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if publication.kind == rtc.TrackKind.KIND_AUDIO:
                publication.set_subscribed(True)

        # Track subscribed event
        @ctx.room.on("track_subscribed")
        async def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"[LiveKit] Subscribed audio from {participant.identity}")
                audio_stream = rtc.AudioStream(track)  # async iterator
                async for frame in audio_stream:
                    # frame: bytes PCM 48kHz linear16
                    await deepgram_stream_audio(frame)

async def main():
    agent = MyAgent()
    await agent.connect(LIVEKIT_URL, LIVEKIT_TOKEN)

    # giữ agent chạy
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
