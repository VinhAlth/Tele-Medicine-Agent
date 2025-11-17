import asyncio
import re
import json
from datetime import datetime
from dotenv import load_dotenv
from typing import AsyncIterable
from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from livekit.plugins import deepgram
from livekit.agents import stt as agents_stt

load_dotenv()

SPEAKER_TAG_RE = re.compile(r"\[SP(\d+)\]\s*(.*)", re.DOTALL)

async def entrypoint(ctx: agents.JobContext):
    speaker_map: dict[str, str] = {}
    observed_speakers: list[str] = []
    transcript: dict[str, list[dict]] = {}

    def _map_speaker(speaker_id: str) -> str:
        if speaker_id in speaker_map:
            return speaker_map[speaker_id]
        if len(observed_speakers) < 2:
            observed_speakers.append(speaker_id)
            label = "Speaker A" if len(observed_speakers) == 1 else "Speaker B"
            speaker_map[speaker_id] = label
            transcript[label] = []
            return label
        speaker_map[speaker_id] = f"Other-{speaker_id}"
        transcript[speaker_map[speaker_id]] = []
        return speaker_map[speaker_id]

    async def process_track(track: rtc.RemoteTrack, participant_name: str):
        stt_core = deepgram.STT(
            model="nova-2",
            language="vi",
            interim_results=True,
            punctuate=True,
            enable_diarization=True
        )

        multi_stt = agents_stt.MultiSpeakerAdapter(
            stt=stt_core,
            detect_primary_speaker=True,
            suppress_background_speaker=False,
            primary_format="[SP{speaker_id}] {text}",
            background_format="[SP{speaker_id}] {text}"
        )

        stt_stream = multi_stt.stream()
        audio_stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)

        async def process_stt_stream(stream: AsyncIterable[SpeechEvent]):
            async for event in stream:
                if event.type in (SpeechEventType.INTERIM_TRANSCRIPT, SpeechEventType.FINAL_TRANSCRIPT) and event.alternatives:
                    text = event.alternatives[0].text.strip()
                    if text:
                        m = SPEAKER_TAG_RE.match(text)
                        if m:
                            spk = m.group(1)
                            body = m.group(2).strip()
                        else:
                            # Nếu không có tag, coi participant là speaker chính
                            spk = participant_name
                            body = text
                        label = _map_speaker(spk)
                        transcript[label].append({
                            "type": "interim" if event.type == SpeechEventType.INTERIM_TRANSCRIPT else "final",
                            "participant": participant_name,
                            "text": body,
                            "time": datetime.now().isoformat()
                        })
                        print(f"[{event.type.name}] {participant_name} ({label}): {body}")

                elif event.type == SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    text = event.alternatives[0].text.strip()
                    if text:
                        for m in SPEAKER_TAG_RE.finditer(text):
                            spk = m.group(1)
                            body = m.group(2).strip()
                            label = _map_speaker(spk)
                            print(f"[FINAL] {participant_name} ({label}): {body}")
                            transcript[label].append({
                                "type": "final",
                                "participant": participant_name,
                                "text": body,
                                "time": datetime.now().isoformat()
                            })

        async with asyncio.TaskGroup() as tg:
            tg.create_task(process_stt_stream(stt_stream))
            async for audio_event in audio_stream:
                stt_stream.push_frame(audio_event.frame)
            stt_stream.end_input()

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        sid = getattr(publication.track, "sid", "unknown") if publication else "unknown"
        print(f"[TRACK] Subscribed to {participant.identity} ({sid})")
        asyncio.create_task(process_track(track, participant.identity))

    # --- shutdown callback sử dụng add_shutdown_callback ---
# --- shutdown callback sử dụng add_shutdown_callback ---
    async def on_shutdown():
        filename = f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        lines = []
        # Duyệt theo speaker
        for speaker_id, items in transcript.items():
            for item in items:
                if item["type"] == "final":
                    time_str = datetime.fromisoformat(item["time"]).strftime("%H:%M:%S")
                    lines.append(f"{speaker_id}: {item['text']} | {time_str}")

        # Sắp xếp theo thời gian
        lines.sort(key=lambda x: x.split("|")[1].strip())

        # Ghi ra file
        with open(filename, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

        print(f"✅ Transcript saved to {filename}")


    ctx.add_shutdown_callback(on_shutdown)

    await ctx.connect()
    print("✅ Agent started, waiting for audio...")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
