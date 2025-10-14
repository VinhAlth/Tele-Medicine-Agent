import os
import requests
from dotenv import load_dotenv
from langchain.schema import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# Load .env
load_dotenv()
CLINIC_API = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/records/CLINIC/danh_sach_phong_kham"
GOOGLE_API_KEY = "AIzaSyCVtFTGivTyzG3DJUu48NUr6RsCI0rfebA"


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

def doctor_advice(user_input: str) -> str:
    """
    Nhận input là text: tên, tuổi, triệu chứng thu thập được
    Trả về text: gợi ý dịch vụ và phòng khám nếu đủ thông tin
    """
    
    # Lấy danh sách phòng khám
    clinics_data = get_clinics()
    clinics_list_text = ""
    if clinics_data["success"]:
        clinics_list_text = "\n".join([f"{c['_id']}: {c['name']}" for c in clinics_data["clinics"]])
    else:
        clinics_list_text = "Không lấy được danh sách phòng khám."

    # Prompt chuẩn bác sĩ
    system_prompt = f"""
Bạn là một bác sĩ hơn 10 năm kinh nghiệm trong chuẩn đoán và đưa ra dịch vụ khám, phòng khám phù hợp. 
- Bệnh nhân cung cấp thông tin: {user_input}
- mục tiêu của bạn là đưa ra kết quả: dịch vụ và danh sách phòng khám phù hợp
- Bạn sẽ:
    1. Đánh giá triệu chứng và tuổi.
    2. Gợi ý dịch vụ phù hợp (ví dụ: khám tổng quát, nội soi, xét nghiệm máu, chụp X quang ...) dựa trên phòng khám.
    3. Gợi ý phòng khám phù hợp từ danh sách sau:
{clinics_list_text}
    4. input là toàn bộ triệu chứng hãy trả về các dịch vụ khám và danh sách phòng khám phù hợp kèm _id phòng của mỗi phòng khám
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
    print(response.content)

# Example sử dụng
if __name__ == "__main__":
    session_id = "session_001"
    user_input = "bệnh nhân 70 tuổi, giới tính nam, triệu chứng: nhức đầu, đau họng, ỉa chảy, càng ăn thì càng đau bụng, tối ngủ không được"
    advice = doctor_advice(user_input)
    print(advice)
