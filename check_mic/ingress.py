import os
import asyncio
import subprocess
import aiohttp
import time
from livekit import api
from dotenv import load_dotenv
load_dotenv()

API_URL = "https://content-core-dev.longvan.vn/api/layouts?filters[sites][name][$eq]=TRUEDOC&filters[name][$eq]=WAITINGROOM&populate[banners]=true"


async def fetch_latest_video_url():
    """Gọi API và lấy URL video mới nhất từ banner WAITINGROOM."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=10) as resp:
                if resp.status != 200:
                    print(f"❌ API lỗi: {resp.status}")
                    return None
                data = await resp.json()

        banners = (
            data.get("data", [{}])[0]
            .get("attributes", {})
            .get("banners", {})
            .get("data", [])
        )

        if not banners:
            print("⚠️ Không có banner nào trong dữ liệu API.")
            return None

        media_items = banners[0]["attributes"].get("media", [])
        videos = [m for m in media_items if m.get("type") == "VIDEO" and m.get("url")]
        if not videos:
            print("⚠️ Không tìm thấy media VIDEO nào.")
            return None

        latest_video = videos[-1]
        video_url = latest_video["url"]

        print(f"🎬 Video mới nhất: {video_url}")
        return video_url

    except Exception as e:
        print(f"❌ Lỗi khi fetch video URL: {e}")
        return None


async def create_ingress_and_push(room_name: str):
    """Tạo ingress RTMP và stream video lặp trong 1 giờ."""
    video_path = await fetch_latest_video_url()
    if not video_path:
        print("❌ Không lấy được video_path hợp lệ, dừng lại.")
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
        name="ingress_agent",
        room_name=room_name,
        participant_identity="ingress_agent",
        participant_name="Giới thiệu về phòng khám",
        participant_metadata='{"is_featured": "true"}',
    )

    ingress = await lkapi.ingress.create_ingress(req)
    full_rtmp = f"{ingress.url}/{ingress.stream_key}"
    print(f"✅ Ingress created: {ingress.ingress_id}")
    print(f"🎥 RTMP endpoint: {full_rtmp}")

    # Chờ 20s cho room ổn định
    await asyncio.sleep(20)

    # Bắt đầu đếm thời gian
    start_time = time.time()
    MAX_DURATION = 36 # 1 giờ

    print("🚀 Bắt đầu stream video lặp trong 1 giờ...")

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= MAX_DURATION:
                print("⏰ Hết 1 tiếng, dừng stream và xóa ingress.")
                break

            cmd = [
                "ffmpeg", "-re",
                "-stream_loop", "-1",
                "-i", video_path,
                "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-f", "flv", full_rtmp,
            ]

            print(f"🧩 Chạy FFmpeg stream loop (thời gian chạy: {int(elapsed)}s)")
            proc = subprocess.Popen(cmd)

            try:
                # Giới hạn thời gian chạy của mỗi vòng là 60s -> nếu ffmpeg lỗi sẽ restart
                await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, proc.wait), timeout=60)
            except asyncio.TimeoutError:
                # FFmpeg vẫn chạy bình thường
                pass
            except Exception as e:
                print(f"⚠️ Lỗi FFmpeg: {e}")
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    await asyncio.sleep(1)
                    if proc.poll() is None:
                        proc.kill()
                        print("🛑 Đã buộc dừng FFmpeg.")
                print("🔁 Restart lại stream vòng kế tiếp...")

            await asyncio.sleep(5)

    finally:
        print("🧹 Dọn ingress...")
        try:
            await lkapi.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress.ingress_id))
            print("✅ Đã xóa ingress sau 1 giờ.")
        except Exception as e:
            print(f"⚠️ Lỗi khi xóa ingress: {e}")


if __name__ == "__main__":
    room = "Phong01"
    asyncio.run(create_ingress_and_push(room))
