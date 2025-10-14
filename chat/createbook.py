import requests
import logging
from datetime import datetime, time

# ==================== CONFIG ==================== #
OWNER_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/records/OWNER/danh_sach_khach_hang"
BOOKING_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/schedule/createBooking/68de058d9219cf7b58c57634"
SLOT_API_BASE = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/schedule/slots/68de058d9219cf7b58c57634"

FIXED_PAYLOAD = {
    "group": "SCHEDULE",
    "partnerId": "TRUEDOC",
    "status": 1,
    "resourceType": "CLINIC",
    "calendarId": "68de058d9219cf7b58c57634",
}

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


def create_booking(phone: str, startDateExpect: str, endDateExpect: str, clinicId: str):
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
        slot_url = f"{SLOT_API_BASE}/{clinicId}/{booking_date}"
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


# ==================== DEMO ==================== #
if __name__ == "__main__":
    res = create_booking(
        phone="09340949444",
        startDateExpect="2025-10-09T16:00:00",
        endDateExpect="2025-10-09T18:00:00",
        clinicId="20.180337.9151",
    )
    print(res)
