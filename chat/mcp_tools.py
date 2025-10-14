import sseclient
import requests
from typing import Type
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

# --- Mô tả Input cho Tool ---
# Bắt buộc phải có để LLM biết cần truyền tham số gì vào tool
class BookAppointmentInput(BaseModel):
    """Input schema for the book_appointment tool."""
    patient_name: str = Field(description="Họ và tên đầy đủ của bệnh nhân.")
    doctor_name: str = Field(description="Tên của bác sĩ mà bệnh nhân muốn đặt lịch.")
    time_slot: str = Field(description="Khung thời gian cụ thể muốn đặt, ví dụ: '9:00 AM - 10:00 AM 15/10/2025'.")
    symptoms: str = Field(description="Mô tả ngắn gọn về triệu chứng của bệnh nhân.")

# --- Custom Tool Class ---
class MCPBookingTool(BaseTool):
    """
    Một tool chuyên dụng để đặt lịch khám bệnh viện thông qua hệ thống MCP
    sử dụng giao thức Server-Sent Events (SSE).
    """
    name: str = "book_hospital_appointment"
    description: str = (
        "Rất hữu ích khi bạn cần thực hiện hành động cuối cùng là đặt lịch khám bệnh viện cho bệnh nhân. "
        "Sử dụng tool này SAU KHI đã thu thập ĐẦY ĐỦ thông tin bao gồm: "
        "tên bệnh nhân, tên bác sĩ, khung giờ và triệu chứng."
    )
    args_schema: Type[BaseModel] = BookAppointmentInput

    def _run(self, patient_name: str, doctor_name: str, time_slot: str, symptoms: str) -> str:
        """
        Phương thức chính để thực thi tool.
        Nó sẽ kết nối đến server MCP qua SSE và trả về kết quả cuối cùng.
        """
        mcp_server_url = "http://45.119.86.209:8000/sse"
        
        # Dữ liệu bạn muốn gửi đi để server MCP xử lý
        # Ở đây ta giả định server nhận POST request để khởi tạo stream
        # hoặc có thể truyền qua params. Tùy vào thiết kế server của bạn.
        payload = {
            "action": "book_appointment",
            "details": {
                "patient_name": patient_name,
                "doctor_name": doctor_name,
                "time_slot": time_slot,
                "symptoms": symptoms
            }
        }
        
        print(f"DEBUG: Đang gửi yêu cầu đến MCP Server với payload: {payload}")

        try:
            # Sử dụng stream=True để giữ kết nối mở cho SSE
            response = requests.post(mcp_server_url, json=payload, stream=True, timeout=30)
            response.raise_for_status() # Báo lỗi nếu status code là 4xx hoặc 5xx
            
            # Khởi tạo client để đọc stream SSE
            client = sseclient.SSEClient(response)
            
            full_message = ""
            print("DEBUG: Đã kết nối SSE, đang lắng nghe events...")

            # Lặp qua các event được server đẩy về
            for event in client.events():
                # Giả sử server của bạn có một event tên là 'confirmation'
                # để báo hiệu đã đặt lịch thành công
                if event.event == 'confirmation':
                    print(f"DEBUG: Nhận được event 'confirmation': {event.data}")
                    full_message = event.data
                    break # Thoát vòng lặp khi có kết quả cuối cùng
                
                # Giả sử có event 'error' để báo lỗi
                elif event.event == 'error':
                    print(f"DEBUG: Nhận được event 'error': {event.data}")
                    full_message = f"Lỗi từ hệ thống đặt lịch: {event.data}"
                    break

                # Giả sử có event 'update' để cập nhật trạng thái
                elif event.event == 'update':
                     print(f"DEBUG: Nhận được event 'update': {event.data}")
                     # Có thể bỏ qua hoặc log lại trạng thái này
                     pass

            if not full_message:
                return "Không nhận được xác nhận từ hệ thống đặt lịch sau 30 giây."
                
            return f"Kết quả từ hệ thống đặt lịch MCP: {full_message}"

        except requests.exceptions.RequestException as e:
            print(f"ERROR: Lỗi kết nối đến MCP Server: {e}")
            return f"Không thể kết nối đến server đặt lịch. Vui lòng thử lại sau. Chi tiết lỗi: {e}"
        except Exception as e:
            print(f"ERROR: Lỗi không xác định khi xử lý SSE: {e}")
            return f"Có lỗi không xác định xảy ra. Chi tiết: {e}"

# Khởi tạo instance của tool để sử dụng trong agent
mcp_tool = MCPBookingTool()