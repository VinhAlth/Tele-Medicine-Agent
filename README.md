<img width="1058" height="276" alt="image" src="https://github.com/user-attachments/assets/93de3481-44f5-42c1-85b2-62c096d1d4b1" /># Tele-Medicine-Agent
Dự án **Tele Medician Agent** xây dựng một voicebot và record agent trên nền **LiveKit + App/Web**.   Hệ thống giúp tiếp nhận bệnh nhân, hỗ trợ đăng ký phiếu khám, tạo mã bệnh nhân để vào phòng khám, nhắc nhở trước giờ hẹn, và ghi lại nội dung tư vấn giữa bác sĩ và bệnh nhân.  
# 🏥 Tele Agent System (LiveKit + App/Web)

## 📌 Giới thiệu
Dự án này xây dựng hệ thống **voicebot + record agent** phục vụ bệnh nhân trong quy trình đặt khám và tư vấn y khoa.  
Hệ thống hoạt động hoàn toàn trên **app/web + LiveKit**.

---

## 🎯 Mục tiêu
- Tiếp nhận bệnh nhân gọi đến qua app/web.  
- Sàng lọc và điều hướng đến trợ lý y khoa.  
- Nhắc nhở bệnh nhân sẵn sàng khám.  
- Tham gia cùng bác sĩ – bệnh nhân để ghi lại nội dung tư vấn.  


## 🔄 Flow Hoạt Động

### 1. Inbound Call (bệnh nhân gọi đến app/web)
- Bệnh nhân mở app/web → gọi hỗ trợ.  
- LiveKit tạo **room random** cho cuộc gọi này.  
- **Tele agent** join vào room → tiếp nhận thông tin bệnh nhân.  
- Điều hướng đến trợ lý y khoa phù hợp.

---

## 2. Outbound Call
- Agent gọi đến các bệnh nhân khi sắp đến giờ khám, để chuẩn bị join phòng.


### 3. Bệnh nhân join phòng khám chính thức
- Bệnh nhân nhập mã bệnh nhân/phòng khám trên app/web.  
- App gửi mã → **backend kiểm tra**:  
  - ✅ **Đúng mã** → cấp token → join đúng room (room = mã bệnh nhân).  
  - ❌ **Sai mã** → từ chối cấp token, báo lỗi: *“Mã bệnh nhân không đúng, vui lòng nhập lại.”*  

---

### 4. Khám bệnh (room chính thức)
- **Bệnh nhân** join room bằng mã bệnh nhân.  
- **Bác sĩ** join cùng room.  
- **Record agent** auto-join → ghi lại nội dung.  
- Kết thúc buổi khám → tất cả rời room → session end.  

---

## 🤖 Các loại Agent

### 🟢 Tele agent
- Bắt inbound call từ app/web (room random).  
- Điều hướng đăng ký phiếu khám.  

### 🟡 Record agent
- Tham gia phòng khám chính thức.  
- Ghi lại nội dung tư vấn giữa bác sĩ và bệnh nhân.  
- Lưu transcript vào DB.  

### 🔵 Reminder agent 
- Kiểm tra lịch khám.  
- Trước 5–10 phút → gửi thông báo / in-app call nhắc bệnh nhân join đúng room.  

---

## ⚙️ Kiến trúc hệ thống

```
<img width="1058" height="276" alt="image" src="https://github.com/user-attachments/assets/fe6c93bb-efb4-4a2c-846f-63c24811dc02" />

[App/Web] → [LiveKit Server] → [Backend]
↘ Tele Agent          ↘ Record Agent

```

- **1 LiveKit server duy nhất** quản lý tất cả room.  
- **Room random** dùng cho inbound call (không quan tâm tên).  
- **Room chính thức** = mã bệnh nhân (được sinh sau khi đăng ký).  
- **Backend** chịu trách nhiệm validate mã trước khi cấp token.  

