import os
import asyncio
import time
from dotenv import load_dotenv
from livekit import api
import requests
import datetime

# --- Load env ---
load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# --- Config ---
RECORD_ROOMS = {f"Phong{i:02}" for i in range(1, 11)}
MIN_PARTICIPANTS = 2
CHECK_INTERVAL = 5  # giây

# --- State ---
egress_map = {}        # room_name -> egress_id
room_recording = {}    # room_name -> bool
room_filepath = {}     # room_name -> current file path

def now():
    return time.strftime("[%H:%M:%S]")

# --- Token helper ---
def create_egress_token(room_name: str) -> str:
    grants = api.VideoGrants(room_record=True, room_join=True, room=room_name)
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.identity = f"egress_agent_{room_name}"
    token.with_grants(grants)
    token.ttl = datetime.timedelta(hours=1)
    return token.to_jwt()

# --- Safe participant list ---
async def safe_list_participants(lkapi, room_name):
    try:
        resp = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return resp.participants
    except Exception as e:
        print(f"{now()} ⚠️ list_participants failed for {room_name}: {repr(e)}")
        return []

# --- Start egress ---
# --- Start egress ---
def start_egress(room_name: str):
    if room_recording.get(room_name, False):
        print(f"{now()} ⏳ Room {room_name} đang record rồi, bỏ qua start.")
        return

    jwt = create_egress_token(room_name)
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = f"clinic/recordings/{room_name}_{now_str}.mp4"

    payload = {
        "room_name": room_name,
        "file_outputs": [{"filepath": filepath, "file_type": "MP4"}],
        "advanced": {
            "width": 1280,
            "height": 720,
            "framerate": 30,
            "video_codec": "H264_MAIN",
            "video_bitrate": 1000,       # kbps
            "key_frame_interval": 4,
            "audio_codec": "AAC",
            "audio_bitrate": 96,
            "audio_frequency": 22050
        }
    }

    url = f"{LIVEKIT_URL}/twirp/livekit.Egress/StartRoomCompositeEgress"

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        egress_id = resp.json().get("egress_id")
        egress_map[room_name] = egress_id
        room_recording[room_name] = True
        room_filepath[room_name] = filepath
        print(f"{now()} 🚀 Room {room_name} có ≥{MIN_PARTICIPANTS} người thực → Bắt đầu record")
        print(f"{now()} 📁 Video sẽ lưu tại: {filepath}")
    except Exception as e:
        print(f"{now()} ❌ Failed to start egress for {room_name}: {repr(e)}")

# --- Stop egress ---
def stop_egress(room_name: str):
    egress_id = egress_map.get(room_name)
    if not egress_id:
        room_recording[room_name] = False
        return

    jwt = create_egress_token(room_name)
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    url = f"{LIVEKIT_URL}/twirp/livekit.Egress/StopEgress"

    try:
        resp = requests.post(url, headers=headers, json={"egress_id": egress_id}, timeout=10)
        if resp.ok:
            print(f"{now()} 🛑 Room {room_name} dưới {MIN_PARTICIPANTS} người thực → Dừng record")
            print(f"{now()} 📁 Video đã lưu tại: {room_filepath.get(room_name)}")
        else:
            print(f"{now()} ❌ Stop failed for {room_name}: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"{now()} ❌ Stop egress error for {room_name}: {repr(e)}")

    room_recording[room_name] = False
    egress_map.pop(room_name, None)
    room_filepath.pop(room_name, None)

# --- Main monitor loop ---
async def monitor_record_rooms():
    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    try:
        while True:
            for room_name in RECORD_ROOMS:
                participants = await safe_list_participants(lkapi, room_name)

                # --- Lọc participant thực, bỏ agent/EG_... ---
                real_participants = [
                    p for p in participants
                    if not (p.identity.startswith("EG_") or p.identity == "ingress_agent")
                ]
                count_real = len(real_participants)

                if count_real == 0:
                    continue  # không in gì nếu không có user thực

                # Khởi tạo trạng thái mặc định nếu chưa có
                if room_name not in room_recording:
                    room_recording[room_name] = False

                recording = room_recording[room_name]

                # --- Debug info ---
                print(f"{now()} 👥 Room {room_name} hiện có {count_real} participant(s) thực: {[p.identity for p in real_participants]}")
                print(f"{now()} Debug: recording={recording}, egress_map={egress_map.get(room_name)}")

                # --- Logic start/stop ---
                if count_real >= MIN_PARTICIPANTS and not recording:
                    start_egress(room_name)
                elif count_real < MIN_PARTICIPANTS and recording:
                    stop_egress(room_name)
                else:
                    print(f"{now()} ⏹ Không thay đổi trạng thái record cho {room_name}")

            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await lkapi.aclose()

# --- Entry point ---
if __name__ == "__main__":
    asyncio.run(monitor_record_rooms())
