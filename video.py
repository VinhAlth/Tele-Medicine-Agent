import os
import asyncio
import subprocess
from dotenv import load_dotenv
from livekit import api

"""
Chiếu video local lên phòng LiveKit bằng RTMP Ingress.
Yêu cầu:
  - Đã cài ffmpeg (`apt install ffmpeg`)
  - Đã cài livekit python sdk (`pip install livekit-agents`)
  - File .env chứa:
      LIVEKIT_URL=https://your-livekit-server
      LIVEKIT_API_KEY=devkey
      LIVEKIT_API_SECRET=devsecret
Cách chạy:
  python video_local_ingress.py /path/to/video.mp4 testroom
"""

load_dotenv()

async def create_ingress_and_push(video_path: str, room_name: str):
    """Tạo ingress RTMP và push video local vào room."""
    if not os.path.exists(video_path):
        print(f"❌ Không tìm thấy file: {video_path}")
        return

    print("🔗 Kết nối LiveKit API...")
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    # Tạo ingress RTMP
    req = api.CreateIngressRequest(
        input_type=api.IngressInput.RTMP_INPUT,
        name="agent_ingress",
        room_name=room_name,
        participant_identity="agent_ingress",
        participant_name="agent_ingress",
        participant_metadata='{"is_featured": "true"}',
    )

    ingress = await lkapi.ingress.create_ingress(req)
    full_rtmp = f"{ingress.url}/{ingress.stream_key}"
    print(f"✅ Ingress created: {ingress.ingress_id}")
    print(f"🎥 RTMP endpoint: {full_rtmp}")

    # Lệnh ffmpeg push video local lên ingress
    cmd = [
        "ffmpeg", "-re", "-stream_loop", "-1",
        "-i", video_path,
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "flv", full_rtmp,
    ]

    print("🚀 Bắt đầu stream video...")
    print("🧩 CMD:", " ".join(cmd))

    try:
        proc = subprocess.Popen(cmd)
        print(f"📺 FFmpeg PID: {proc.pid}")
        await asyncio.get_event_loop().run_in_executor(None, proc.wait)
        print("🛑 FFmpeg kết thúc.")
    finally:
        print("🧹 Dọn ingress...")
        await lkapi.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress.ingress_id))
        print("✅ Đã xóa ingress.")

async def main():
    import sys
    if len(sys.argv) < 3:
        print("Cách dùng: python video_local_ingress.py /path/to/video.mp4 room_name")
        return

    video_path = sys.argv[1]
    room_name = sys.argv[2]
    await create_ingress_and_push(video_path, room_name)

if __name__ == "__main__":
    asyncio.run(main())
