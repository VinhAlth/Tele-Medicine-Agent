#!/usr/bin/env python3
"""
LiveKit Agent: Detect mic/camera ON/OFF when participants join or toggle.
"""

import os
import asyncio
import logging
from dotenv import load_dotenv

import livekit.agents as agents
from livekit.agents import WorkerOptions
from livekit import rtc

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("agent_check_cam")


def _kind_to_str(kind):
    """Normalize track kind to lowercase string."""
    try:
        return kind.name.lower()
    except Exception:
        return str(kind).lower()


async def participant_entrypoint(ctx: agents.JobContext, participant: rtc.RemoteParticipant):
    """Check and log participant's current mic/camera status."""
    identity = getattr(participant, "identity", None) or getattr(participant, "sid", "<unknown>")
    logger.info(f"[JOIN] Participant detected: {identity}")

    try:
        pubs = list(participant.tracks.values()) if getattr(participant, "tracks", None) else []
    except Exception:
        pubs = []

    camera_on = False
    mic_on = False

    for pub in pubs:
        kind_str = _kind_to_str(getattr(pub, "kind", ""))
        is_muted = bool(getattr(pub, "muted", False))

        if "video" in kind_str or "camera" in kind_str:
            camera_on = camera_on or (not is_muted)
        if "audio" in kind_str:
            mic_on = mic_on or (not is_muted)

    cam_status = "ON" if camera_on else "OFF"
    mic_status = "ON" if mic_on else "OFF"
    logger.info(f"[STATUS] {identity} → CAMERA: {cam_status} | MIC: {mic_status}")


async def entrypoint(ctx: agents.JobContext):
    """Main job entrypoint."""
    logger.info("🚀 Entry point starting, connecting to LiveKit room...")
    await ctx.connect()

    # 🔹 1️⃣ Gọi thủ công cho những participant đã có trong room
    try:
        existing = list(ctx.room.remote_participants.values())
    except Exception:
        existing = []
    if existing:
        logger.info(f"🔍 Found {len(existing)} participant(s) already in the room, checking initial states...")
        for p in existing:
            await participant_entrypoint(ctx, p)
    else:
        logger.info("No existing participants found (waiting for new joins).")

    # 🔹 2️⃣ Đăng ký callback cho user join mới
    ctx.add_participant_entrypoint(participant_entrypoint)

    # 🔹 3️⃣ Event handlers cho bật/tắt mic & cam real-time
    @ctx.room.on("track_muted")
    def _on_track_muted(participant, publication):
        p_id = getattr(participant, "identity", getattr(participant, "sid", "<unknown>"))
        kind = _kind_to_str(getattr(publication, "kind", ""))
        label = "CAMERA" if "video" in kind else "MIC" if "audio" in kind else kind.upper()
        logger.info(f"[UPDATE] {p_id} → {label}: OFF")

    @ctx.room.on("track_unmuted")
    def _on_track_unmuted(participant, publication):
        p_id = getattr(participant, "identity", getattr(participant, "sid", "<unknown>"))
        kind = _kind_to_str(getattr(publication, "kind", ""))
        label = "CAMERA" if "video" in kind else "MIC" if "audio" in kind else kind.upper()
        logger.info(f"[UPDATE] {p_id} → {label}: ON")

    # 🔹 4️⃣ Shutdown handler
    shutdown_event = asyncio.Event()

    async def _on_shutdown(reason: str = ""):
        logger.info(f"🛑 Shutdown requested: {reason}")
        shutdown_event.set()

    ctx.add_shutdown_callback(_on_shutdown)

    logger.info("✅ Agent connected and ready — waiting for participants & track events...")
    await shutdown_event.wait()
    logger.info("Agent exiting (shutdown).")



if __name__ == "__main__":
    opts = WorkerOptions(
        entrypoint_fnc=entrypoint,
        ws_url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    agents.cli.run_app(opts) 
