# stt_deepgram_agent.py
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import aiohttp
import traceback

from livekit import agents as lk_agents
from livekit.agents import JobContext
import livekit.rtc as rtc
from livekit.plugins import deepgram

load_dotenv()

# ----------------- CONFIG -----------------
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # if not set, webhook calls are skipped
BOT_ID = os.getenv("BOT_ID", "deepgram-bot")
FIXED_TOPIC_ID = os.getenv("FIXED_TOPIC_ID", "test-topic")
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "0.5"))  # buffer duration to push (seconds)
MIN_ENERGY = int(os.getenv("MIN_AUDIO_ENERGY", "1000"))  # filter near-silence buffers
INTERIM_RESULTS = False  # set True if you want interim transcripts (for debug)

if not DEEPGRAM_API_KEY:
    raise RuntimeError("DEEPGRAM_API_KEY not found in env")

BYTES_PER_SAMPLE = 2  # 16-bit PCM

# ----------------- HELPERS -----------------
async def send_webhook(payload: dict):
    if not WEBHOOK_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=payload) as resp:
                await resp.text()  # ignore body
    except Exception as e:
        # don't spam; just print a short message
        print(f"[WEBHOOK ERROR] {e}", flush=True)

def make_frame_wrapper(data_bytes: bytes, sample_rate: int = SAMPLE_RATE, num_channels: int = 1):
    """Return an object with attributes expected by some STT SDK variants."""
    class FrameWrapper:
        def __init__(self, data, sample_rate, num_channels):
            self.data = data
            self.sample_rate = sample_rate
            self.num_channels = num_channels
            # 16-bit PCM => 2 bytes per sample per channel
            self.samples_per_channel = max(1, len(data) // (2 * max(1, num_channels)))
        def __repr__(self):
            return f"<FrameWrapper sr={self.sample_rate} ch={self.num_channels} samples={self.samples_per_channel}>"
    return FrameWrapper(data_bytes, sample_rate, num_channels)

async def consume_stt_stream_iterable(stt_stream, participant_id: str):
    """Consume STT async iterable; print and webhook final transcripts only."""
    try:
        async for ev in stt_stream:
            # extract transcript robustly
            text = None
            if getattr(ev, "alternatives", None):
                # prefer first non-empty alternative
                for alt in ev.alternatives:
                    text = getattr(alt, "transcript", None) or getattr(alt, "text", None) or text
                    if text:
                        break
            else:
                text = getattr(ev, "transcript", None) or getattr(ev, "text", None) or text

            is_final = bool(getattr(ev, "is_final", False) or getattr(ev, "isFinal", False))
            if text and text.strip() and is_final:
                ts = datetime.utcnow().isoformat()
                # minimal console output: participant + text
                print(f"[TRANSCRIPT][{participant_id}] {text}", flush=True)
                # send webhook
                payload = {
                    "senderName": participant_id,
                    "senderId": participant_id,
                    "type": "text",
                    "content": text,
                    "timestamp": ts,
                    "botId": BOT_ID,
                    "topicId": FIXED_TOPIC_ID,
                    "isMessageInGroup": False,
                    "meta": {"is_final": True}
                }
                await send_webhook(payload)
    except Exception as e:
        print(f"[STT CONSUMER ERROR] {participant_id}: {e}", flush=True)
        traceback.print_exc()

def register_stt_on_callback(stt_stream, participant_id: str):
    """
    If stream supports .on('transcript', cb), register a callback that posts final transcripts.
    Returns (task, queue) but callback writes directly to webhook/print.
    """
    def _on_trans(ev):
        try:
            text = None
            if getattr(ev, "alternatives", None):
                for alt in ev.alternatives:
                    text = getattr(alt, "transcript", None) or getattr(alt, "text", None) or text
                    if text:
                        break
            else:
                text = getattr(ev, "transcript", None) or getattr(ev, "text", None) or text
            is_final = bool(getattr(ev, "is_final", False) or getattr(ev, "isFinal", False))
            if text and text.strip() and is_final:
                ts = datetime.utcnow().isoformat()
                print(f"[TRANSCRIPT][{participant_id}] {text}", flush=True)
                payload = {
                    "senderName": participant_id,
                    "senderId": participant_id,
                    "type": "text",
                    "content": text,
                    "timestamp": ts,
                    "botId": BOT_ID,
                    "topicId": FIXED_TOPIC_ID,
                    "isMessageInGroup": False,
                    "meta": {"is_final": True}
                }
                # schedule webhook async (don't await in callback)
                asyncio.create_task(send_webhook(payload))
        except Exception as e:
            print(f"[STT CALLBACK ERROR] {participant_id}: {e}", flush=True)
            traceback.print_exc()

    try:
        stt_stream.on("transcript", _on_trans)
        return True
    except Exception:
        return False

# ----------------- MAIN ENTRYPOINT -----------------
async def entrypoint(ctx: JobContext):
    print("[INFO] Connecting to room...", flush=True)
    await ctx.connect()
    room_name = getattr(ctx.room, "name", "unknown")
    print(f"[INFO] Connected to room: {room_name}", flush=True)
    print("[READY] Listening for audio tracks from all participants...", flush=True)

    # create deepgram plugin instance
    stt_plugin = deepgram.STT(
        api_key=DEEPGRAM_API_KEY,
        model="nova-2",
        language="vi",
        interim_results=INTERIM_RESULTS
    )

    # compute buffer threshold in bytes
    BUFFER_BYTES = int(SAMPLE_RATE * BYTES_PER_SAMPLE * BUFFER_SECONDS)

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant: rtc.RemoteParticipant):
        # only audio tracks
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        pid = participant.identity or participant.sid or "unknown"
        print(f"[TRACK] Subscribed audio from {pid}", flush=True)
        asyncio.create_task(handle_audio_track(track, pid, stt_plugin, BUFFER_BYTES))

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        pid = participant.identity or participant.sid or "unknown"
        print(f"[DISCONNECT] Participant {pid} disconnected", flush=True)

    # keep process alive
    while True:
        await asyncio.sleep(1)


# ----------------- PER-PARTICIPANT TRACK HANDLER -----------------
async def handle_audio_track(track: rtc.RemoteTrack, participant_id: str, stt_plugin: deepgram.STT, buffer_bytes_threshold: int):
    """
    - create consumer BEFORE pushing frames
    - aggregate small frames into buffer (buffer_seconds)
    - skip near-silence buffers
    - try push bytes; fallback to object wrapper if SDK insists on attributes
    - only print/send final transcripts
    """
    SAMPLE_RATE_LOCAL = SAMPLE_RATE
    NUM_CHANNELS = 1
    # create stream
    try:
        stt_stream = stt_plugin.stream()
    except Exception as e:
        print(f"[ERROR] cannot create stt stream for {participant_id}: {e}", flush=True)
        return

    # start consumer BEFORE pushing frames
    consumer_task = None
    used_callback = False
    if hasattr(stt_stream, "__aiter__"):
        consumer_task = asyncio.create_task(consume_stt_stream_iterable(stt_stream, participant_id))
    else:
        # try register callback-style; if succeed, we don't need extra task
        ok = register_stt_on_callback(stt_stream, participant_id)
        used_callback = ok
        if not ok:
            # try iterable as fallback
            if hasattr(stt_stream, "__aiter__"):
                consumer_task = asyncio.create_task(consume_stt_stream_iterable(stt_stream, participant_id))

    buffer_parts = []
    pushed_frames = 0
    wrapper_used = False

    try:
        async for frame_event in rtc.AudioStream(track):
            frame = getattr(frame_event, "frame", None)
            if frame is None:
                continue

            # extract bytes
            data = getattr(frame, "data", None)
            if data is None:
                try:
                    data = bytes(frame)
                except Exception:
                    continue
            if isinstance(data, memoryview):
                data = data.tobytes()

            # simple energy check: sum absolute sample values (16-bit)
            try:
                energy = 0
                # process as 16-bit signed little-endian
                for i in range(0, len(data), 2):
                    sample = int.from_bytes(data[i:i+2], "little", signed=True)
                    energy += abs(sample)
            except Exception:
                energy = 0

            if energy < MIN_ENERGY:
                # skip low-energy frames
                continue

            buffer_parts.append(data)
            total_len = sum(len(p) for p in buffer_parts)
            if total_len < buffer_bytes_threshold:
                continue

            # we have enough buffered audio (~BUFFER_SECONDS) -> push
            buffer_bytes = b"".join(buffer_parts)
            buffer_parts.clear()

            # push bytes, fallback wrapper if needed
            try:
                stt_stream.push_frame(buffer_bytes)
                pushed_frames += 1
            except Exception as e_push:
                msg = str(e_push)
                # if sdk complains about missing sample_rate or similar, fallback to wrapper
                if ("sample_rate" in msg) or ("sampleRate" in msg) or ("sample_rate" in msg) or ("samples_per_channel" in msg) or ("sampleRate" in msg):
                    try:
                        wrapper = make_frame_wrapper(buffer_bytes, sample_rate=SAMPLE_RATE_LOCAL, num_channels=NUM_CHANNELS)
                        stt_stream.push_frame(wrapper)
                        pushed_frames += 1
                        wrapper_used = True
                    except Exception as e2:
                        print(f"[ERROR] fallback wrapper push failed for {participant_id}: {e2}", flush=True)
                        # don't crash; continue
                        continue
                else:
                    # other push error -> print once and continue
                    print(f"[ERROR] pushing frame for {participant_id}: {e_push}", flush=True)
                    continue

    except Exception as e:
        print(f"[AUDIO STREAM ERROR] {participant_id}: {e}", flush=True)
        traceback.print_exc()

    # push remaining buffer if any
    if buffer_parts:
        buffer_bytes = b"".join(buffer_parts)
        try:
            stt_stream.push_frame(buffer_bytes)
            pushed_frames += 1
        except Exception:
            try:
                wrapper = make_frame_wrapper(buffer_bytes, sample_rate=SAMPLE_RATE_LOCAL, num_channels=NUM_CHANNELS)
                stt_stream.push_frame(wrapper)
                pushed_frames += 1
                wrapper_used = True
            except Exception as efinal:
                print(f"[ERROR] final push failed for {participant_id}: {efinal}", flush=True)

    # signal end to STT
    try:
        stt_stream.end_input()
    except Exception as e:
        print(f"[ERROR] end_input for {participant_id}: {e}", flush=True)

    # wait consumer if applicable
    if consumer_task:
        try:
            await consumer_task
        except Exception as e:
            print(f"[ERROR] stt consumer for {participant_id} crashed: {e}", flush=True)
            traceback.print_exc()

    # final minimal report
    print(f"[DONE] {participant_id} pushed_frames={pushed_frames} wrapper_used={wrapper_used}", flush=True)


# ----------------- RUNNER -----------------
if __name__ == "__main__":
    # auto-run agent entrypoint
    lk_agents.cli.run_app(lk_agents.WorkerOptions(entrypoint_fnc=entrypoint))
