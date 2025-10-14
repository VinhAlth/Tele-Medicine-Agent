from fastmcp import FastMCP
import requests
from datetime import datetime, time
import logging
import requests
import logging
from datetime import datetime, time
#from dateutil import parser
import os
import requests
from dotenv import load_dotenv
from langchain.schema import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI



# ================================================ #
mcp = FastMCP("clinic-booking-mcp")
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# API endpoints
CUSTOMER_API = "https://user.dev.longvan.vn/user-gateway/graphql"
OWNER_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/records/OWNER/danh_sach_khach_hang"
CLINIC_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/records/CLINIC/danh_sach_phong_kham"
SLOT_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/schedule/slots/68de058d9219cf7b58c57634"
BOOKING_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/schedule/createBooking/68de058d9219cf7b58c57634"

# --- 1. Tải cấu hình từ môi trường ---
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Các giá trị cố định
FIXED_PAYLOAD = {
    "group": "SCHEDULE",
    "partnerId": "TRUEDOC",
    "status": 1,
    "resourceType": "CLINIC",
    "calendarId": "68de058d9219cf7b58c57634",
}

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== FUNCTION ==================== #
def parse_time(t: str) -> time:
    return datetime.strptime(t, "%H:%M").time()

# ================================================ #
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
def save_customer(name: str, phone: str, email: str = None):
    """
    🔹 Kiểm tra khách hàng theo số điện thoại.
    Nếu chưa có, tạo mới trong hệ thống.
    - name: bắt buộc
    - phone: bắt buộc
    - email: tùy chọn
    """
    try:
        # --- Check khách hàng đã tồn tại ---
        query = {"query": f'{{ customers(phone: "{phone}") {{ id name phone email }} }}'}
        res = requests.post(CUSTOMER_API, json=query, timeout=5)
        res.raise_for_status()
        customers = res.json().get("data", {}).get("customers", [])
        if customers:
            return {"success": True, "data": customers[0], "msg": "Khách hàng đã tồn tại."}

        # --- Nếu chưa có, tạo mới ---
        fields = [f'name: "{name}"', f'phone: "{phone}"', 'partnerId: "TRUEDOC"', 'createdBy: "system"']
        if email:
            fields.append(f'email: "{email}"')

        mutation = {"query": f"mutation {{ createCustomer({', '.join(fields)}) {{ id name phone email }} }}"}
        res = requests.post(CUSTOMER_API, json=mutation, timeout=5)
        res.raise_for_status()
        created = res.json().get("data", {}).get("createCustomer")
        return {"success": True, "data": created, "msg": "Tạo khách hàng mới thành công."}

    except Exception as e:
        logger.exception("Lỗi khi lưu khách hàng:")
        return {"success": False, "error": str(e)}


# ================================================ #
#@mcp.tool()
def get_clinics():
    """🏥 Lấy danh sách phòng khám khả dụng."""
    try:
        res = requests.get(CLINIC_API, timeout=10)
        res.raise_for_status()
        data = res.json()
        clinics = [{"_id": c["_id"], "name": c["name"]} for c in data]
        return {"success": True, "clinics": clinics}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ================================================ #

@mcp.tool()
def check_slot(clinicId: str, bookingDate: str):
    """
    ⏰ Lấy tất cả slot trống trong ngày cho phòng khám.
    - clinicId: _id phòng khám (từ get_clinics)
    - bookingDate: Ngày đặt lịch (YYYY-MM-DD)
    
    ✅ Output:
    {
        "success": True/False,
        "msg": "Mô tả kết quả",
        "slots": [
            {"fromTime": "07:00", "toTime": "09:00", "availableSlot": 2},
            ...
        ]
    }
    """
    try:
        # ✅ Ghép đúng endpoint: BASE + /{clinicId}/{bookingDate}
        slot_url = f"{SLOT_API}/{clinicId}/{bookingDate}"
        logger.info(f"🔍 Gọi API slot: {slot_url}")

        response = requests.get(slot_url, timeout=10)
        logger.debug(f"🔹 Raw Response ({response.status_code}): {response.text[:200]}")

        # Kiểm tra lỗi HTTP
        response.raise_for_status()

        # Parse JSON
        try:
            data = response.json()
        except ValueError:
            return {
                "success": False,
                "error": f"Phản hồi không phải JSON hợp lệ: {response.text[:200]}"
            }

        # Dữ liệu hợp lệ dạng list
        slots = data if isinstance(data, list) else data.get("data", [])
        free_slots = [
            {
                "fromTime": s.get("fromTime"),
                "toTime": s.get("toTime"),
                "availableSlot": s.get("availableSlot", 0)
            }
            for s in slots
            if s.get("status") == "ACTIVE" and s.get("availableSlot", 0) > 0
        ]

        if not free_slots:
            return {
                "success": False,
                "msg": f"Không có slot trống nào trong ngày {bookingDate}.",
                "slots": []
            }

        return {
            "success": True,
            "msg": f"Tổng {len(free_slots)} slot trống trong ngày {bookingDate}.",
            "slots": free_slots
        }

    except requests.RequestException as e:
        logger.error(f"❌ Lỗi kết nối tới API slot: {e}")
        return {"success": False, "error": f"Lỗi kết nối API: {str(e)}"}

    except Exception as e:
        logger.exception("❌ Lỗi khi xử lý check_slot:")
        return {"success": False, "error": str(e)} 




logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


def parse_time(t: str) -> time:
    return datetime.strptime(t, "%H:%M").time()


def parse_iso_datetime(s: str) -> datetime:
    if not isinstance(s, str):
        raise ValueError("Datetime phải là chuỗi ISO")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    raise ValueError(f"Không parse được datetime: {s}")


def get_clinics_2():
    """🏥 Lấy danh sách phòng khám khả dụng."""
    try:
        res = requests.get(CLINIC_API, timeout=10)
        res.raise_for_status()
        data = res.json()
        clinics = [{"_id": c["_id"], "name": c["name"]} for c in data]
        return {"success": True, "clinics": clinics}
    except Exception as e:
        return {"success": False, "error": str(e)}
    

@mcp.tool()
def doctor_advice(user_input: str) -> str:
    """
    Nhận input là text: tên, tuổi, lý do khám, triệu chứng thu thập được
    Trả về text: gợi ý dịch vụ khám phù hợp
    """
    try:
        # Prompt chuẩn bác sĩ
        system_prompt = f"""
Bạn là một bác sĩ hơn 10 năm kinh nghiệm trong chuẩn đoán và đưa ra dịch vụ khám phù hợp tại Phòng Khám Đa Khoa Jio Health. 
- Bệnh nhân cung cấp thông tin: {user_input}
- mục tiêu của bạn là đưa ra kết quả: các dịch vụ khám phù hợp dựa trên thông tin input
- Bạn sẽ:
    1. Đánh giá triệu chứng và tuổi, giới tính,lý do khám bệnh.
    2. Gợi ý dịch vụ phù hợp (ví dụ: khám tổng quát, nội soi, xét nghiệm máu, chụp X quang ...).
    5. trả lời ngắn gọn
- Trả lời dưới dạng **text**, không JSON
"""

        # Khởi tạo Google Generative AI LLM
        llm = ChatGoogleGenerativeAI(api_key=GOOGLE_API_KEY, model="gemini-2.5-flash")

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input)
        ]

        response = llm.invoke(messages)

        # Luôn trả về string
        return str(response.content)

    except Exception as e:
        # Bất kỳ lỗi nào cũng trả về string để MCP không fail
        return f"Có lỗi khi gọi LLM: {str(e)}"


# ==================== FUNCTION ==================== #

@mcp.tool()
def create_booking(phone: str, startDateExpect: str, endDateExpect: str, clinicId: str):
    """
    Đặt lịch khám tự động theo slot thực tế (dạng ISO datetime).

    Input:
        - phone (str): Số điện thoại khách hàng để xác định chủ booking.
        - startDateExpect (str): Thời gian bắt đầu mong muốn, định dạng ISO 
          (ví dụ: "2025-10-07T14:00:00").
        - endDateExpect (str): Thời gian kết thúc mong muốn, định dạng ISO 
          (ví dụ: "2025-10-07T16:00:00").
        - clinicId (str): ID phòng khám (là id lấy từ API get_clinics()).

    Logic xử lý:
        - Lấy danh sách khách hàng theo SĐT.
        - Gọi API slot để lấy các khung giờ còn trống trong ngày.
        - Tự động xác định slot hợp lệ chứa khoảng thời gian yêu cầu.
        - Nếu slot đó còn chỗ (availableSlot > 0), tạo booking.
        - Nếu slot đầy hoặc ngoài giờ làm việc → trả về lỗi kèm khung giờ gợi ý.
    """

    try:
        # 1️⃣ Lấy thông tin khách hàng
        resp = requests.get(OWNER_API, timeout=10)
        resp.raise_for_status()
        owners = resp.json()
        owner = next((o for o in owners if o.get("phone") == phone), None)
        if not owner:
            return {"status": "FAILED", "message": f"Không tìm thấy khách hàng với SĐT {phone}"}
        owner_id = owner["_id"]
        owner_name = owner.get("name", "Không rõ")
        logger.info(f"🔍 Tìm thấy khách hàng: {owner_name} ({owner_id})")

        # 2️⃣ Parse thời gian
        start_dt = parse_iso_datetime(startDateExpect)
        end_dt = parse_iso_datetime(endDateExpect)
        if start_dt.date() != end_dt.date():
            return {"status": "FAILED", "message": "StartDate và EndDate phải cùng ngày."}

        f_in, t_in = start_dt.time(), end_dt.time()
        fromTime_val, toTime_val = start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M")
        booking_date = start_dt.date().isoformat()

        # 3️⃣ Gọi Slot API
        slot_url = f"{SLOT_API}/{clinicId}/{booking_date}"
        logger.info(f"🔍 Gọi slot API: {slot_url}")
        slot_resp = requests.get(slot_url, timeout=10)
        slot_resp.raise_for_status()
        slots = slot_resp.json()
        slots = slots if isinstance(slots, list) else slots.get("data", [])

        if not slots:
            return {"status": "FAILED", "message": "Không có dữ liệu slot trong ngày này."}

        sorted_slots = sorted(slots, key=lambda s: parse_time(s["fromTime"]))
        start_work = parse_time(sorted_slots[0]["fromTime"])
        end_work = parse_time(sorted_slots[-1]["toTime"])

        # 4️⃣ Kiểm tra slot phù hợp
        chosen_slot = None
        full_slot = None

        for s in sorted_slots:
            f_slot = parse_time(s["fromTime"])
            t_slot = parse_time(s["toTime"])
            if f_in >= f_slot and t_in <= t_slot:
                if s.get("availableSlot", 0) > 0 and s.get("status") == "ACTIVE":
                    chosen_slot = s
                    break
                else:
                    full_slot = s
                    break

        # 5️⃣ Không tìm được slot trống
        if not chosen_slot:
            if full_slot:
                msg = f"❌ Khung giờ {full_slot['fromTime']}-{full_slot['toTime']} đã hết chỗ."
                suggested = [f"{s['fromTime']}-{s['toTime']}" for s in sorted_slots if s.get("availableSlot", 0) > 0]
                return {"status": "FAILED", "message": msg, "suggested_slots": suggested}

            if t_in <= start_work or f_in >= end_work:
                msg = "🌙 Ngoài giờ làm việc của phòng khám."
                suggested = [f"{s['fromTime']}-{s['toTime']}" for s in sorted_slots if s["availableSlot"] > 0]
                return {"status": "FAILED", "message": msg, "suggested_slots": suggested}

            gaps = []
            for i in range(len(sorted_slots) - 1):
                end_prev = parse_time(sorted_slots[i]["toTime"])
                start_next = parse_time(sorted_slots[i + 1]["fromTime"])
                if end_prev < start_next:
                    gaps.append((end_prev, start_next))

            if any(f_in >= g[0] and t_in <= g[1] for g in gaps):
                msg = "🕑 Đây là giờ nghỉ giữa các ca."
            else:
                msg = "⚠️ Không có khung giờ hoạt động phù hợp cho thời gian bạn yêu cầu."

            suggested = [f"{s['fromTime']}-{s['toTime']}" for s in sorted_slots if s["availableSlot"] > 0]
            return {"status": "FAILED", "message": msg, "suggested_slots": suggested}

        # 6️⃣ Tạo booking thành công
        shift_id = chosen_slot["shiftId"]
        payload = {
            **FIXED_PAYLOAD,
            "resourceId": clinicId,
            "startDateExpect": startDateExpect,
            "endDateExpect": endDateExpect,
            "fromTime": fromTime_val,
            "toTime": toTime_val,
            "ownerId": owner_id,
            "shiftId": shift_id,
        }

        headers = {"Content-Type": "application/json"}
        response = requests.post(BOOKING_API, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        booking_data = response.json()

        # 🧾 Log chi tiết
        logger.info(
            f"✅ Đăng ký thành công cho {owner_name} ({phone})\n"
            f"   ⏰ Giờ yêu cầu: {fromTime_val}-{toTime_val}\n"
            f"   📅 Trong khung slot: {chosen_slot['fromTime']}-{chosen_slot['toTime']} "
            f"(Shift: {shift_id})\n"
            f"   📘 Booking ID: {booking_data.get('id')}"
        )

        return {"status": "SUCCESS", "booking": booking_data}

    except requests.exceptions.RequestException as e:
        logger.exception("❌ Lỗi kết nối API: %s", e)
        return {"status": "ERROR", "message": f"Lỗi kết nối API: {e}"}
    except Exception as e:
        logger.exception("❌ Lỗi khi tạo booking: %s", e)
        return {"status": "ERROR", "message": str(e)}


# ================================================ #
if __name__ == "__main__":
    # Chạy local trong ứng dụng (off)
    # Nếu muốn expose HTTP thì đổi sang transport="sse"
    #mcp.run(transport="local")
    mcp.run(transport="sse", host="0.0.0.0", port=9003)
