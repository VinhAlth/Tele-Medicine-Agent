# 🏥 TeleMedician – Trợ lý AI đặt lịch khám tại phòng khám

**TeleMedician** là hệ thống Trợ lý Y khoa AI giúp **bệnh nhân đặt lịch khám tự động** thông qua hội thoại giọng nói hoặc chat.  
Dự án kết hợp giữa **AI hội thoại, LiveKit Agent** và **FastAPI** để cung cấp trải nghiệm đặt lịch nhanh, tự nhiên, có **con người hỗ trợ khi cần (human-in-the-loop)**.

---

## 🚀 Tính năng chính

- 🤖 **Trợ lý AI tự động hỏi thông tin bệnh nhân**
  - Năm sinh, họ tên, giới tính, thông tin liên hệ
- 💬 **Trao đổi lý do khám & triệu chứng**
  - AI sẽ hỏi thêm chi tiết để hiểu rõ tình trạng
- 🧠 **Gợi ý dịch vụ khám phù hợp**
  - Dựa trên triệu chứng hoặc lựa chọn của bệnh nhân
- 📅 **Đăng ký lịch khám trực tiếp**
  - Gửi thông tin đặt lịch đến hệ thống/nhân viên hỗ trợ
- 👩‍⚕️ **Human in the loop**
  - Nếu AI không chắc chắn hoặc cần xác nhận, hệ thống sẽ mời **nhân viên y tế** tiếp nhận

---

## 🧩 Luồng hội thoại (Flow)

```text
Bệnh nhân → AI hỏi thông tin cơ bản
    ↓
AI hỏi lý do khám / triệu chứng
    ↓
AI gợi ý dịch vụ phù hợp (hoặc hỏi thêm nếu chưa đủ)
    ↓
Bệnh nhân chọn dịch vụ / xác nhận thời gian
    ↓
AI tạo lịch hẹn → lưu hệ thống → gửi xác nhận
    ↓
Nếu không xử lý được → chuyển cho người thật (Human in loop)
