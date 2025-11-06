import requests
from datetime import datetime
import pytz
from mcp.server.fastmcp import FastMCP

# --- Khởi tạo MCP server ---
mcp = FastMCP("BookingTools")

# --- URL API hệ thống Booking ---
SAVE_USER_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/records/Booking/form_nhap_lieu"

# --- Định nghĩa tool MCP theo spec MCP 2025-06-18 ---
@mcp.tool(
    name="save_user",
    description="Lưu thông tin người dùng vào hệ thống Booking",
    # Note: fastmcp không hỗ trợ full inputSchema, type hints + docstring là đủ
)
def save_user(contact_name: str, contact_phone: str, session_id: str):
    """
    Tool lưu thông tin user vào hệ thống Booking.
    Trả về JSON nội bộ để backend/agent xử lý.
    
    Args:
        contact_name (str): Tên người dùng
        contact_phone (str): Số điện thoại
        session_id (str): ID phiên làm việc
    """
    # --- Validation ---
    if not contact_name or not contact_phone or not session_id:
        return {"success": False, "error": "contact_name, contact_phone, session_id are required"}

    payload = {
        "contactName": contact_name,
        "contactPhone": contact_phone,
        "sessionId": session_id,
        "group": "BOOKING",
        "status": 1,
        "timestamp": datetime.utcnow().replace(tzinfo=pytz.UTC).isoformat()
    }

    # --- Gọi API ---
    try:
        resp = requests.post(SAVE_USER_API, json=payload, timeout=5)
        resp.raise_for_status()
        return {"success": True, "api_response": resp.json()}
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}

# --- Chạy MCP server ---
if __name__ == "__main__":
    print("🚀 MCP server đang chạy, chờ client gọi tool...")
    mcp.run()
