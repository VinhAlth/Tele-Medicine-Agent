import subprocess
import asyncio
import os
from livekit import api

async def create_ingress_and_push_two_videos(room_name: str, video1: str, video2: str):
    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    # Tạo ingress cho video 1
    req1 = api.CreateIngressRequest(
        input_type=api.IngressInput.RTMP_INPUT,
        name="KỂ CHUYỆN KINH TẾ HỌC",
        room_name=room_name,
        participant_identity="KỂ CHUYỆN KINH TẾ HỌC",
        participant_name="KỂ CHUYỆN KINH TẾ HỌC",
    )
    ingress1 = await lkapi.ingress.create_ingress(req1)
    full_rtmp1 = f"{ingress1.url}/{ingress1.stream_key}"

    # Tạo ingress cho video 2
    req2 = api.CreateIngressRequest(
        input_type=api.IngressInput.RTMP_INPUT,
        name="KỂ CHUYỆN CỘNG SẢN",
        room_name=room_name,
        participant_identity="KỂ CHUYỆN CỘNG SẢN",
        participant_name="KỂ CHUYỆN CỘNG SẢN",
    )
    ingress2 = await lkapi.ingress.create_ingress(req2)
    full_rtmp2 = f"{ingress2.url}/{ingress2.stream_key}"

    # Thông báo bắt đầu
    print(f"▶️ Đang stream video 1 vào room {room_name}: {full_rtmp1}")
    print(f"▶️ Đang stream video 2 vào room {room_name}: {full_rtmp2}")

    # Khởi FFmpeg cho video 1
    cmd1 = [
        "ffmpeg", "-re",
        "-stream_loop", "-1",
        "-i", video1,
        "-vf", "scale=1280:720",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "1300k", "-maxrate", "1500k", "-bufsize", "2200k",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "22050",
        "-f", "flv", full_rtmp1,
    ]

    # Khởi FFmpeg cho video 2
    cmd2 = [
        "ffmpeg", "-re",
        "-stream_loop", "-1",
        "-i", video2,
        "-vf", "scale=1280:720",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "1300k", "-maxrate", "1500k", "-bufsize", "2200k",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "22050",
        "-f", "flv", full_rtmp2,
    ]

    # Giấu log
    devnull = open(os.devnull, "wb")
    proc1 = subprocess.Popen(cmd1, stdout=devnull, stderr=devnull)
    proc2 = subprocess.Popen(cmd2, stdout=devnull, stderr=devnull)

    # Chạy trong 1 giờ (có thể điều chỉnh)
    await asyncio.sleep(3600)

    # Terminate nếu còn chạy
    for proc in (proc1, proc2):
        if proc.poll() is None:
            proc.terminate()

    # Xóa ingress
    await lkapi.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress1.ingress_id))
    await lkapi.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress2.ingress_id))

    print("✅ Stream cả 2 video đã kết thúc và ingress bị xóa.")

# Example gọi
if __name__ == "__main__":
    video1 = "/root/AGENT/Tele_Medician/recording/audio/kechuyenvekinhtehoc.mp4"
    video2 = "/root/AGENT/Tele_Medician/recording/audio/Kechuyenvecongssan.mp4"
    room = "Offline05"
    asyncio.run(create_ingress_and_push_two_videos(room, video1, video2))
