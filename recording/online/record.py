import asyncio
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents.stt import SpeechEventType, SpeechEvent
from typing import AsyncIterable, Dict, List
from livekit.plugins import deepgram

load_dotenv()

# --- Configuration tunables ---
CONSUMER_TIMEOUT_SEC = 12        # nếu không nhận FINAL trong X giây -> cleanup
INACTIVITY_CLOSE_SEC = 30       # nếu không nhận audio frame lâu -> đóng stream
SESSION_FLUSH_INTERVAL = 10     # in/gom transcript theo session mỗi X giây

# --- Simple session manager to aggregate final transcripts per session ---
class SessionManager:
    def __init__(self):
        # map session_id -> list of (timestamp, speaker, text)
        self.sessions: Dict[str, List[tuple]] = {}
        self._lock = asyncio.Lock()

    async def add_final(self, session_id: str, speaker: str, text: str):
        async with self._lock:
            self.sessions.setdefault(session_id, []).append((datetime.utcnow(), speaker, text))
            print(f"[SESSION] [{session_id}] appended FINAL from {speaker}: {text}")

    async def flush_session(self, session_id: str):
        async with self._lock:
            items = self.sessions.pop(session_id, [])
        if not items:
            print(f"[SESSION] [{session_id}] flush: (empty)")
            return
        # Simple aggregation: print chronological
        print(f"[SESSION] ===== FLUSH {session_id} ({len(items)} items) =====")
        for ts, speaker, text in items:
            ts_s = ts.isoformat()
            print(f"[SESSION] {ts_s} | {speaker}: {text}")
        print(f"[SESSION] ===== END FLUSH {session_id} =====")

    async def periodic_flush_all(self):
        while True:
            await asyncio.sleep(SESSION_FLUSH_INTERVAL)
            async with self._lock:
                keys = list(self.sessions.keys())
            for k in keys:
                await self.flush_session(k)

session_manager = SessionManager()

# --- Entrypoint ---
async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    print("✅ Agent connected and ready")

    # start periodic flusher
    asyncio.create_task(session_manager.periodic_flush_all(), name="session-periodic-flush")

    # track -> worker task map so we can cancel if unsubscribed
    active_track_tasks: Dict[str, asyncio.Task] = {}

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.RemoteTrack, publication, participant):
        identity = participant.identity
        track_id = f"{identity}__{publication.track_sid if hasattr(publication, 'track_sid') else id(track)}"
        print(f"[EVENT] track_subscribed: track_id={track_id} identity={identity}")
        t = asyncio.create_task(process_track(track, identity, track_id, ctx.room.name if hasattr(ctx.room, 'name') else "room"), name=f"track-{track_id}")
        active_track_tasks[track_id] = t

    @ctx.room.on("track_unsubscribed")
    def on_track_unsubscribed(track: rtc.RemoteTrack, publication, participant):
        identity = participant.identity
        track_id = f"{identity}__{publication.track_sid if hasattr(publication, 'track_sid') else id(track)}"
        print(f"[EVENT] track_unsubscribed: track_id={track_id} identity={identity}")
        task = active_track_tasks.pop(track_id, None)
        if task:
            print(f"[EVENT] cancelling task for {track_id}")
            task.cancel()

    async def process_track(track: rtc.RemoteTrack, speaker: str, track_id: str, session_id: str):
        """
        Per-track processing:
         - create a Deepgram STT stream
         - producer: read frames from AudioStream and push_frame (stop when stt_closed set)
         - consumer: iterate events from stt_stream
         - If either side finishes/errors -> cleanup, ensure end_input called, cancel other side
         - If FINAL transcript, push to session_manager
         - Detailed logs for debugging multi-speaker stop issues
        """
        print(f"[START] process_track start: track_id={track_id} speaker={speaker} session={session_id}")
        stt = deepgram.STT(model="nova-2", language="vi")
        stt_stream = stt.stream()
        audio_stream = rtc.AudioStream(track)

        stt_closed = asyncio.Event()   # set when end_input called or stream closed
        last_event_time = datetime.utcnow()
        last_frame_time = datetime.utcnow()

        # Helper to mark end_input exactly once
        def safe_end_input():
            if not stt_closed.is_set():
                try:
                    stt_stream.end_input()
                    print(f"[INFO] [{track_id}] stt_stream.end_input() called")
                except Exception as e:
                    print(f"[WARN] [{track_id}] end_input exception: {e}")
                stt_closed.set()

        # consumer reads events from deepgram stream
        async def consumer():
            nonlocal last_event_time
            try:
                async for event in stt_stream:
                    last_event_time = datetime.utcnow()
                    try:
                        if event.type == SpeechEventType.FINAL_TRANSCRIPT:
                            text = event.alternatives[0].text.strip()
                            if text:
                                print(f"[FINAL] [{track_id}] {speaker}: {text}")
                                # add to session aggregation
                                await session_manager.add_final(session_id, speaker, text)
                        elif event.type == SpeechEventType.INTERIM_TRANSCRIPT:
                            text = event.alternatives[0].text.strip()
                            if text:
                                print(f"[INTERIM] [{track_id}] {speaker}: {text}")
                        else:
                            print(f"[DEBUG] [{track_id}] STT event: {event.type}")
                    except Exception:
                        print(f"[ERROR] [{track_id}] processing event failed:\n{traceback.format_exc()}")
            except asyncio.CancelledError:
                print(f"[INFO] [{track_id}] consumer cancelled")
                raise
            except Exception as e:
                print(f"[ERROR] [{track_id}] consumer exception: {e}\n{traceback.format_exc()}")
                raise
            finally:
                # consumer ended normally -> ensure stt closed
                safe_end_input()
                print(f"[INFO] [{track_id}] consumer finished/closing")

        # producer reads audio frames and push to deepgram
        async def producer():
            nonlocal last_frame_time
            try:
                async for audio_event in audio_stream:
                    last_frame_time = datetime.utcnow()
                    # if stt stream already closed, break early to avoid push_frame errors
                    if stt_closed.is_set():
                        print(f"[DEBUG] [{track_id}] producer detected stt_closed -> breaking")
                        break
                    try:
                        stt_stream.push_frame(audio_event.frame)
                        # light log for frames (throttle to reduce noise)
                        if (datetime.utcnow() - last_frame_time) < timedelta(seconds=0.5):
                            # don't print every frame; only occasionally
                            pass
                    except Exception as e:
                        # push_frame can fail if stream closed; log and break
                        print(f"[WARN] [{track_id}] push_frame failed: {e}")
                        # ensure we mark closed and break
                        safe_end_input()
                        break
            except asyncio.CancelledError:
                print(f"[INFO] [{track_id}] producer cancelled")
                # if cancelled, still ensure STT closes to flush final
                safe_end_input()
                raise
            except Exception as e:
                print(f"[ERROR] [{track_id}] producer exception: {e}\n{traceback.format_exc()}")
                safe_end_input()
                raise
            finally:
                # Normal end of audio_stream -> close stt input
                safe_end_input()
                print(f"[INFO] [{track_id}] producer finished/closing")

        consumer_task = asyncio.create_task(consumer(), name=f"consumer-{track_id}")
        producer_task = asyncio.create_task(producer(), name=f"producer-{track_id}")

        # monitor task to handle timeouts/inactivity
        async def monitor():
            try:
                while True:
                    await asyncio.sleep(1)
                    now = datetime.utcnow()
                    # if no STT events for a while -> consider consumer stalled
                    if (now - last_event_time).total_seconds() > CONSUMER_TIMEOUT_SEC and not stt_closed.is_set():
                        print(f"[TIMEOUT] [{track_id}] no STT event for {CONSUMER_TIMEOUT_SEC}s -> triggering safe_end_input")
                        safe_end_input()
                    # if no audio frames for long -> close
                    if (now - last_frame_time).total_seconds() > INACTIVITY_CLOSE_SEC and not stt_closed.is_set():
                        print(f"[INACTIVITY] [{track_id}] no audio frames for {INACTIVITY_CLOSE_SEC}s -> safe_end_input")
                        safe_end_input()
                    # if both tasks done -> break
                    if consumer_task.done() or producer_task.done():
                        break
            except asyncio.CancelledError:
                print(f"[INFO] [{track_id}] monitor cancelled")
                raise

        monitor_task = asyncio.create_task(monitor(), name=f"monitor-{track_id}")

        # wait until either producer or consumer finishes; then cancel the other
        try:
            done, pending = await asyncio.wait(
                {consumer_task, producer_task, monitor_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # log which finished
            for d in done:
                print(f"[DONE] [{track_id}] finished: {d.get_name()} result={None if d.cancelled() else getattr(d, 'result', 'n/a')}")
        except Exception as e:
            print(f"[ERROR] [{track_id}] wait exception: {e}\n{traceback.format_exc()}")
        finally:
            # Cancel any pending tasks
            for t in (consumer_task, producer_task, monitor_task):
                if not t.done():
                    t.cancel()
            # ensure stt closed and flush
            safe_end_input()

            # wait for them to finish
            results = await asyncio.gather(consumer_task, producer_task, monitor_task, return_exceptions=True)
            print(f"[CLEANUP] [{track_id}] gather results: {results}")

            # flush session entries for this session (optional)
            await session_manager.flush_session(session_id)

            print(f"[COMPLETE] process_track ended: track_id={track_id} speaker={speaker} session={session_id}")

    # keep agent running
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        print("[SHUTDOWN] entrypoint cancelled; shutting down")

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
