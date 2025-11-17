import os
import asyncio
import time
import json
import datetime
from dotenv import load_dotenv
from livekit import api
import requests

# optional redis (synchronous client). We'll call it in a thread to avoid blocking the event loop.
try:
    import redis
except Exception:
    redis = None

# --- Load env ---
load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# ------ Redis connection defaults (override with env if you prefer) ------
REDIS_HOST = os.getenv("REDIS_HOST", "redis-connect.dev.longvan.vn")
REDIS_PORT = int(os.getenv("REDIS_PORT", "32276"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "111111aA")
REDIS_HASH_KEY = os.getenv("REDIS_HASH_KEY", "room:online")
# -----------------------------------------------------------------------

# Danh sách phòng cho từng loại agent (static)
MEDICAL_ROOMS = {f"PhongDangKy{i:02}" for i in range(1, 3)}
ASSISTANT_ROOMS = {f"PhongKham{i:02}" for i in range(1, 3)}
# NOTE: đây là các room dùng cho dispatch record_agent trong code gốc
RECORD_ROOMS = {f"PhongHop{i:02}" for i in range(1, 2)}
TEST_ROOMS = {f"Test{i:02}" for i in range(1, 3)}
OFFLINE_ROOMS ={f"Offline{i:02}" for i in range(1,2)}
# --- Egress-specific rooms (static sample from original egress code) ---
# Bạn có thể thay đổi thành {f"Phong{i:02}" for i in range(1,11)} nếu muốn
EGRESS_ROOMS = {f"Phong{i:02}" for i in range(1, 11)}

# ROOMS_TO_MONITOR ban đầu (static); sau này khi chạy sẽ kết hợp với redis_rooms động
STATIC_ROOMS_TO_MONITOR = TEST_ROOMS | ASSISTANT_ROOMS

# Ghi nhớ các phòng đã được dispatch
dispatched_rooms = set()
doctor_first_rooms = set() 

# --- Egress state ---
egress_map = {}        # room_name -> egress_id
room_recording = {}    # room_name -> bool
room_filepath = {}     # room_name -> current file path

# track last known participant identities & counts to reduce logging
last_room_state = {}   # room_name -> {"count_all": int, "count_egress": int, "identities": set(), "recording": bool}

# Config
MIN_PARTICIPANTS_EGRESS = 2   # egress start condition (real users, excluding EG_* and *_agent)
CHECK_INTERVAL = 1            # main loop sleep seconds

def now():
    return time.strftime("[%H:%M:%S]")

# --- Token helper (egress) ---
def create_egress_token(room_name: str) -> str:
    grants = api.VideoGrants(room_record=True, room_join=True, room=room_name)
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.identity = f"egress_agent_{room_name}"
    token.with_grants(grants)
    token.ttl = datetime.timedelta(hours=1)
    return token.to_jwt()

# --- Redis helper (synchronous) ---
def fetch_redis_room_names_sync():
    rooms = set()
    if redis is None:
        # no redis module installed
        return rooms

    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        all_fields = r.hgetall(REDIS_HASH_KEY)  # trả về dict {field: value_json}
        for field, value_json in all_fields.items():
            try:
                data = json.loads(value_json)
                room_name = data.get("roomName")
                if room_name:
                    rooms.add(room_name)
            except json.JSONDecodeError:
                # skip bad field
                continue
    except Exception:
        # don't spam logs; return empty set on error
        return set()
    return rooms

async def fetch_redis_room_names():
    return await asyncio.to_thread(fetch_redis_room_names_sync)

# --- DFlispatch agent ---
async def dispatch_agent(lkapi, room_name: str, agent_name: str):
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
        )
        print(f"{now()} ✅ Dispatched {agent_name} -> {room_name}")
        dispatched_rooms.add(room_name)
    except Exception as e:
        print(f"{now()} ❌ Error dispatching {agent_name} to {room_name}: {repr(e)}")

# --- Safe wrappers ---
async def safe_list_rooms(lkapi):
    try:
        resp = await lkapi.room.list_rooms(api.ListRoomsRequest())
        return resp
    except Exception as e:
        # minimal logging
        print(f"{now()} ⚠️ list_rooms failed: {repr(e)}")
        return None

async def safe_list_participants(lkapi, room_name):
    try:
        resp = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return resp
    except Exception as e:
        print(f"{now()} ⚠️ list_participants failed for room={room_name}: {repr(e)}")
        return None

async def safe_remove_participant(lkapi, room_name, identity, label):
    try:
        await lkapi.room.remove_participant(
            api.RoomParticipantIdentity(room=room_name, identity=identity)
        )
        print(f"{now()} ❌ Removed {label} (identity={identity}) from {room_name}")
        return True
    except Exception as e:
        # TwirpError not_found => participant đã bị remove trước đó
        err_str = str(e)
        if "not_found" in err_str.lower():
            print(f"{now()} ℹ️ {label} (identity={identity}) was already removed from {room_name}")
        else:
            print(f"{now()} ❌ Failed to remove {label} (identity={identity}) in {room_name}: {repr(e)}")
        return False


# --- Participant counting helpers ---
def identity_str(p):
    return ((p.identity or "") if hasattr(p, "identity") else str(p))

def count_all_participants(participants):
    """Count raw participants (used for dispatch logic)."""
    return len(participants)

def count_real_for_egress(participants):
    """
    Count participants excluding:
      - identities starting with "EG_"
      - identities that end with "_agent"
      - explicit "ingress_agent" (also matches *_agent but keep explicit)
    This is the count used to decide start/stop egress (i.e. real human users).
    """
    cnt = 0
    for p in participants:
        pid = (p.identity or "").strip()
        if not pid:
            continue
        if pid.startswith("EG_"):
            continue
        if pid.endswith("_agent"):
            continue
        if pid == "ingress_agent":
            continue
        cnt += 1
    return cnt



# --- Ingress integration ---
import aiohttp
import subprocess

API_URL = "https://content-core-dev.longvan.vn/api/layouts?filters[sites][name][$eq]=TRUEDOC&filters[name][$eq]=WAITINGROOM&populate[banners]=true"

async def fetch_latest_video_url():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=10) as resp:
                if resp.status != 200:
                    print(f"{now()} ❌ API lỗi: {resp.status}")
                    return None
                data = await resp.json()

        banners = (
            data.get("data", [{}])[0]
            .get("attributes", {})
            .get("banners", {})
            .get("data", [])
        )

        if not banners:
            print(f"{now()} ⚠️ Không có banner nào trong dữ liệu API.")
            return None

        media_items = banners[0]["attributes"].get("media", [])
        videos = [m for m in media_items if m.get("type") == "VIDEO" and m.get("url")]
        if not videos:
            print(f"{now()} ⚠️ Không tìm thấy media VIDEO nào.")
            return None

        latest_video = videos[-1]
        video_url = latest_video["url"]
        print(f"{now()} 🎬 Video mới nhất: {video_url}")
        return video_url
    except Exception as e:
        print(f"{now()} ❌ Lỗi khi fetch video URL: {e}")
        return None

## Ingress video 
import subprocess
import asyncio
import os
from livekit import api

async def create_ingress_and_push(room_name: str):
    video_path = await fetch_latest_video_url()
    if not video_path:
        return

    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    req = api.CreateIngressRequest(
        input_type=api.IngressInput.RTMP_INPUT,
        name="ingress_agent",
        room_name=room_name,
        participant_identity="ingress_agent",
        participant_name="Video giới thiệu",
    )
    ingress = await lkapi.ingress.create_ingress(req)
    full_rtmp = f"{ingress.url}/{ingress.stream_key}"

    # ✅ Dòng 1: thông báo bắt đầu
    print(f"{now()} ▶️ Đang stream video vào {room_name}...")

    cmd = [
        "ffmpeg", "-re",
        "-stream_loop", "-1",
        "-i", video_path,
        "-vf", "scale=1280:720",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", "1300k",
        "-maxrate", "1500k",
        "-bufsize", "2200k",
        "-c:a", "aac",
        "-b:a", "96k",
        "-ac", "2",
        "-ar", "22050",
        "-f", "flv", full_rtmp,
    ]

    # ẩn toàn bộ log của ffmpeg
    with open(os.devnull, "wb") as devnull:
        proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)

    await asyncio.sleep(3600)  # chạy 1 giờ

    if proc.poll() is None:
        proc.terminate()

    await lkapi.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress.ingress_id))

    # ✅ Dòng 2: thông báo kết thúc
    print(f"{now()} ✅ Stream đã kết thúc và ingress bị xóa.")



# trạng thái ingress theo room
ingress_state = {}  # room_name -> bool (đã trigger hay chưa)

def normalize(text: str) -> str:
    """Chuẩn hóa chuỗi về dạng thường, bỏ khoảng trắng và dấu tiếng Việt."""
    import unicodedata
    if not text:
        return ""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return text


async def trigger_ingress_if_needed(lkapi, room_name: str, participants):
    """
    Trigger ingress khi chỉ còn 1 user thật trong phòng (không phải bác sĩ).
    Sau khi trigger, đánh dấu. Nếu room trống/reset, sẽ cho trigger lại.
    """
    if room_name not in ingress_state:
        ingress_state[room_name] = False

    # đếm số lượng real user (gọi hàm đã có)
    real_count = count_real_for_egress(participants)

    # lọc ra các user thật KHÔNG phải bác sĩ
    real_users = [
        p for p in participants
        if not any(
            k in normalize(p.identity or "") or k in normalize(p.name or "")
            for k in ["bs", "bacsi", "bac si", "bac-si", "bac_sĩ"]
        )
        and not (p.identity or "").startswith("EG_")
        and not (p.identity or "").endswith("_agent")
        and (p.identity or "").strip() != "ingress_agent"
    ]

    # trigger ingress nếu chỉ còn 1 user thật KHÔNG phải bác sĩ
    if not ingress_state[room_name] and len(real_users) == 1:
        user = real_users[0]
        pname = user.name or user.identity or ""
        pidentity = user.identity or ""
        print(f"{now()} 🎯 Room {room_name} có đúng 1 user thật (không phải bác sĩ): "
              f"pname={pname}, pidentity={pidentity}, real_count={real_count} → trigger ingress...")
        ingress_state[room_name] = True
        asyncio.create_task(create_ingress_and_push(room_name))



# reset trạng thái khi room trống / dispatch reset
def reset_room_ingress_state(room_name: str):
    ingress_state[room_name] = False
    print(f"{now()} 🧹 Reset ingress state cho room {room_name}")


# --- Egress functions (start/stop) ---
def start_egress(room_name: str):
    # protect double-start
    if room_recording.get(room_name, False):
        return

    try:
        jwt = create_egress_token(room_name)
    except Exception as e:
        print(f"{now()} ❌ Failed create egress token for {room_name}: {repr(e)}")
        return

    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = f"default/recordings/{room_name}_{now_str}.mp4"

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
        # log only on change
        print(f"{now()} 🚀 Egress started for {room_name} (file: {filepath})")
    except Exception as e:
        print(f"{now()} ❌ Failed to start egress for {room_name}: {repr(e)}")

def stop_egress(room_name: str):
    egress_id = egress_map.get(room_name)
    if not egress_id:
        room_recording[room_name] = False
        return

    try:
        jwt = create_egress_token(room_name)
    except Exception as e:
        print(f"{now()} ❌ Failed create egress token for stop {room_name}: {repr(e)}")
        # still clear local state to avoid stuck
        room_recording[room_name] = False
        egress_map.pop(room_name, None)
        room_filepath.pop(room_name, None)
        return

    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    url = f"{LIVEKIT_URL}/twirp/livekit.Egress/StopEgress"

    try:
        resp = requests.post(url, headers=headers, json={"egress_id": egress_id}, timeout=10)
        if resp.ok:
            print(f"{now()} 🛑 Egress stopped for {room_name}. Saved: {room_filepath.get(room_name)}")
        else:
            print(f"{now()} ❌ Stop egress failed for {room_name}: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"{now()} ❌ Stop egress error for {room_name}: {repr(e)}")

    room_recording[room_name] = False
    egress_map.pop(room_name, None)
    room_filepath.pop(room_name, None)

# --- Logic to disconnect specific agents (original function) ---
async def disconnect_specific_agents_in_tests(lkapi, rooms_to_monitor):
    resp = await safe_list_rooms(lkapi)
    if resp is None:
        return

    for room_info in resp.rooms:
        room_name = getattr(room_info, "name", "")
        if room_name not in rooms_to_monitor:
            continue

        num = getattr(room_info, "num_participants", 0)

        participants_resp = await safe_list_participants(lkapi, room_name)
        if participants_resp is None:
            continue

        # --- Logic: nếu có cả ingress_agent và assistant_agent, remove assistant_agent sau 5s ---
        ingress_exists = any((p.identity or "").strip() == "ingress_agent" for p in participants_resp.participants)
        assistant_exists = any((p.identity or "").strip() == "assistant_agent" for p in participants_resp.participants)

        if ingress_exists and assistant_exists:
            # copy room_name và danh sách participant identities
            delayed_room_name = room_name
            assistant_identities = [
                (p.identity or "").strip() for p in participants_resp.participants if (p.identity or "").strip() == "assistant_agent"
            ]

            async def delayed_remove(room_name_to_remove, identities_to_remove):
                await asyncio.sleep(5)
                for pid in identities_to_remove:
                    await safe_remove_participant(lkapi, room_name_to_remove, pid, pid)
                    print(f"{now()} ⏱ Removed assistant_agent after delay in {room_name_to_remove}")

            asyncio.create_task(delayed_remove(delayed_room_name, assistant_identities))

        # global set track task đang chạy
        record_agent_pending_remove = set()

        # ở trong monitor/disconnect logic
        real_count = count_real_for_egress(participants_resp.participants)
        identities = [(p.identity or "").strip() for p in participants_resp.participants]

        if real_count == 1 and "record_agent" in identities and room_name not in record_agent_pending_remove:
            record_agent_pending_remove.add(room_name)

            # chỉ truyền room_name và identity, không truyền participants_resp
            async def delayed_remove_record(room_name_to_remove, identity_to_remove):
                await asyncio.sleep(30)
                await safe_remove_participant(lkapi, room_name_to_remove, identity_to_remove, identity_to_remove)
                print(f"{now()} ⏱ Removed record_agent due to lone real participant in {room_name_to_remove}")
                record_agent_pending_remove.discard(room_name_to_remove)

            asyncio.create_task(delayed_remove_record(room_name, "record_agent"))


        # --- Trường hợp tổng > 3 ---
        if num >= 3:
            kicked_count = 0
            for p in participants_resp.participants:
                pid = (p.identity or "").strip()
                pname = (p.name or "").strip()

                if pid == "assistant_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                elif pid == "ingress_agent":
                    ok = await safe_remove_participant(lkapi, room_name, pid, pname)
                    if ok:
                        kicked_count += 1
                        await dispatch_agent(lkapi, room_name, "record_agent")

            if kicked_count > 0:
                print(f"{now()} ℹ️ Removed {kicked_count} participant(s) in {room_name}.")

        # --- Trường hợp chỉ còn 1 người là ingress_agent ---
        elif num == 1:
            if not participants_resp.participants:
                continue
            p = participants_resp.participants[0]
            pid = (p.identity or "").strip()
            pname = (p.name or "").strip()
            if pid == "ingress_agent":
                await safe_remove_participant(lkapi, room_name, pid, pname)
                print(f"{now()} ℹ️ Removed lone ingress_agent in {room_name}")

# --- Main loop (merge dispatch + egress monitor) ---
async def monitor_and_dispatch():
    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    try:
        while True:
            # --- fetch dynamic redis rooms ---
            try:
                redis_rooms = await fetch_redis_room_names()
                redis_rooms.add("clinic")  # thêm room cố định
            except Exception:
                redis_rooms = set()

            # compose rooms to monitor for disconnect logic
            rooms_to_monitor = set(STATIC_ROOMS_TO_MONITOR) | set(redis_rooms)

            # list all rooms
            resp = await safe_list_rooms(lkapi)
            if resp is None:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # For egress: consider EGRESS_ROOMS U dynamic redis rooms
            egress_candidate_rooms = set(EGRESS_ROOMS) | set(redis_rooms)

            # iterate rooms for dispatch decisions
            for room in resp.rooms:
                room_name = getattr(room, "name", "")
                num_participants = getattr(room, "num_participants", 0)

                # --- Dispatch logic: dispatch agents to rooms with participants (preserve original behavior) ---
                if num_participants > 0 and room_name not in dispatched_rooms:
                    # Nếu chỉ có 1 participant, kiểm tra kỹ xem có phải bác sĩ hay ingress_agent
                    if num_participants == 1:
                        participants_resp = await safe_list_participants(lkapi, room_name)
                        if not participants_resp or not participants_resp.participants:
                            continue

                        only_p = participants_resp.participants[0]
                        pid = (only_p.identity or "").strip()
                        pname = (only_p.name or "").strip()
                        pid_norm = pid.lower()
                        pname_norm = pname.lower()
                        # Bỏ qua nếu là ingress_agent
                        if pid == "ingress_agent":
                            continue
                        #Tạm thời để vậy để test offline
                        # if "bsvinh" in pid.lower() or "bsvinh" in pname.lower():
                        #     if room_name not in dispatched_rooms:
                        #         await dispatch_agent(lkapi, room_name, "record")
                        #     continue
                        # ✅ Nếu người đầu tiên là bác sĩ (có 'bs' trong tên/identity, không phân biệt hoa thường)
                        if "bs" in pid.lower() or "bs" in pname.lower():
                            if room_name not in doctor_first_rooms:
                                doctor_first_rooms.add(room_name)
                                print(f"{now()} 👨‍⚕️ Room {room_name}: bác sĩ vào trước → không dispatch agent (chỉ log 1 lần).")
                            continue




                    agent_name = None
                    if room_name in MEDICAL_ROOMS:
                        agent_name = "medical_agent"
                    elif room_name in ASSISTANT_ROOMS:
                        agent_name = "assistant_agent"
                    elif room_name in RECORD_ROOMS:
                        agent_name = "record_agent"
                    elif room_name in TEST_ROOMS:
                        agent_name = "test_agent"
                    elif room_name in redis_rooms:
                        agent_name = "assistant_agent"
                    elif room_name in OFFLINE_ROOMS:
                        agent_name ="record"

                    if agent_name:
                        await dispatch_agent(lkapi, room_name, agent_name)

            # Clean up dispatched_rooms (if room empty or gone)
            for room_name in list(dispatched_rooms):
                room_obj = next((r for r in resp.rooms if getattr(r, "name", None) == room_name), None)
                if not room_obj or getattr(room_obj, "num_participants", 0) == 0:
                    dispatched_rooms.remove(room_name)
                    print(f"{now()} 🧹 Reset dispatch state for empty room {room_name}")

            # --- disconnect-specific-agents logic (original) ---
            await disconnect_specific_agents_in_tests(lkapi, rooms_to_monitor)

            # --- Egress monitor logic ---
            # For each candidate egress room (static or dynamic), check participants and decide start/stop
            for room_name in egress_candidate_rooms:
                participants_resp = await safe_list_participants(lkapi, room_name)
                if participants_resp is None:
                    # if API failure, skip
                    continue

                participants = participants_resp.participants

                count_all = count_all_participants(participants)
                count_for_egress = count_real_for_egress(participants)
                identities = set((p.identity or "").strip() for p in participants)

                prev = last_room_state.get(room_name, {})
                prev_count_all = prev.get("count_all", None)
                prev_count_egress = prev.get("count_egress", None)
                prev_recording = prev.get("recording", False)

                # init per-room recording state if missing
                if room_name not in room_recording:
                    room_recording[room_name] = False

                recording = room_recording[room_name]

                # Only log when something changed (counts or identities or recording state)
                changed = False
                if prev_count_all != count_all or prev_count_egress != count_for_egress or prev_recording != recording or prev.get("identities") != identities:
                    changed = True

                # store new state
                last_room_state[room_name] = {
                    "count_all": count_all,
                    "count_egress": count_for_egress,
                    "identities": identities,
                    "recording": recording
                }

                if not changed:
                    # nothing to do/log for this room this loop
                    continue

                # Minimal logging on changes
                print(f"{now()} 🔎 Room {room_name} changed: total={count_all}, real_for_egress={count_for_egress}, recording={recording}, ids={sorted(list(identities))}")

                if len(participants) == 0:
                    reset_room_ingress_state(room_name)
                # --- Gọi ingress khi có assistant_agent ---
                await trigger_ingress_if_needed(lkapi, room_name, participants)

                # Start egress: only when there are >= MIN_PARTICIPANTS_EGRESS **real** users (excl agents/EG_*)
                if count_for_egress >= MIN_PARTICIPANTS_EGRESS and not recording:
                    start_egress(room_name)

                # Stop egress: when real users count drops below threshold and it was recording
                elif count_for_egress < MIN_PARTICIPANTS_EGRESS and recording:
                    stop_egress(room_name)

            # sleep small interval
            await asyncio.sleep(CHECK_INTERVAL)

    finally:
        await lkapi.aclose()

# --- Entrypoint ---
if __name__ == "__main__":
    asyncio.run(monitor_and_dispatch())
