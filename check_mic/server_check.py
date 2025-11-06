from livekit import api
from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import subprocess
import asyncio

# Tải biến môi trường từ file .env
load_dotenv()

# Khởi tạo MCP agent
mcp = FastMCP("video_ingress_agent")

@mcp.tool()
async def exit_room(ctx, session, reason="MCP trigger"):
    """
    MCP gọi hàm này để yêu cầu agent tự out room.
    """
    print(f"[INFO] Agent chuẩn bị rời room, lý do: {reason}")

    # Gửi lời cảm ơn user trước khi out (tuỳ chọn)
    await session.generate_reply(
        instructions="Cảm ơn bạn! Mic và Camera đã ổn, vui lòng đợi bác sĩ vào khám."
    )

    # Disconnect agent
    await ctx.disconnect()
    print(f"[INFO] Agent đã rời room: {ctx.room.name}")

@mcp.tool()
async def create_ingress_and_push(room_name: str):
    video_path ="/root/AGENT/TeleMedician_voice_oke/2371009702801908938.mp4" #"https://s3-hcm-r1.longvan.net/clinic/default/video/AiHealth-Bac-si-Rieng-Giai-phap-cham-soc.mp4"

    # Nếu video_path là URL thì bỏ qua check local
    if video_path.startswith("http"):
        print(f"ℹ️ Streaming từ URL: {video_path}")
    else:
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

    # Delay 10 giây trước khi bắt đầu chiếu video
    #print("⏱ Đang chờ 10 giây trước khi chiếu video...")
    await asyncio.sleep(20)
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
if __name__ == "__main__":
    # Chạy local trong ứng dụng (off)
    # Nếu muốn expose HTTP thì đổi sang transport="sse"
    #mcp.run(transport="local")
    mcp.run(transport="sse", host="0.0.0.0", port=9004)