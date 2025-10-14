import os
import logging
from dotenv import load_dotenv
from typing import Dict
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_mcp_adapters.client import MultiServerMCPClient

# Cấu hình logging để dễ dàng debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 1. Tải cấu hình từ môi trường ---
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "sse") # Mặc định là 'sse'

if not GOOGLE_API_KEY or not MCP_SERVER_URL:
    raise ValueError("Vui lòng thiết lập GOOGLE_API_KEY và MCP_SERVER_URL trong file .env")

# --- 2. Khởi tạo LLM và MCP Client ---
# Sử dụng model Gemini 1.5 Flash - mạnh mẽ và hiệu quả
# (Tên chính thức là 1.5, không phải 2.5)
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3)

# Khởi tạo MCP Client với cấu hình từ .env
mcp_client = MultiServerMCPClient({
    "clinic_server": {
        "transport": MCP_TRANSPORT,
        "url": MCP_SERVER_URL,
    }
})

# --- 3. Prompt Engineering: Trái tim của Trợ lý Y khoa AI ---
# Prompt này được thiết kế để khớp với các tool bạn đã cung cấp
system_prompt = """
Bạn là "An Tâm", một Trợ lý Y khoa AI cao cấp, chuyên nghiệp và thân thiện.
Nhiệm vụ của bạn là hướng dẫn người dùng qua quy trình đặt lịch khám bệnh online một cách liền mạch bằng các công cụ sau:
- 'get_current_time' luôn gọi lần đầu tiên để cập nhật thời gian.
- `save_customer`: Lưu thông tin cơ bản của người dùng (như tên, SĐT, email (opion)).
- 'doctor_advice': đưa ra dịch vụ, bệnh viện khám phù hợp, phải chắc chắn gọi trước bước gợi ý dịch vụ, trước khi gợi phải hỏi thật kỹ (2,3 câu) nếu chưa thông tin triệu chứng chưa rõ.
- `get_clinics' : danh sách phòng khám, chỉ dùng khi quên _id phòng khám, sẽ là dạng: 20.180xxxxx (nếu bạn không biết số này thì mới gọi)
- 'check_slot': kiểm tra lịch trống phòng khám
- 'create_booking': đăng ký


**QUY TRÌNH BẮT BUỘC BẠN PHẢI TUÂN THEO:**

1.  **Thu thập thông tin:**
    - gọi 'get_current_time' để biết thời gian hiện tại, ngay khi nhận tin nhắn đầu tiên.
    - Hỏi thân thiện về họ tên, năm sinh, giới tính và số điện thoại.
    - Ngay sau khi có đủ thông tin, hãy gọi tool `save_customer` để lưu lại, nhưng không nói là đã lưu thông tin với khách hàng.

2.  **Tư vấn và Kiểm tra lịch:**
    - Hỏi người dùng họ muốn đi khám. và triệu chứng họ đang gặp phải (hỏi kĩ, nếu chưa đủ thông tin cứ hỏi thêm (bước cần trước khi đưa thông tin cho 'doctor_advice')
    - phải đủ thông tin triệu chứng trước khi, gọi tool 'doctor_advise'
    - gợi ý danh dịch vụ khám và danh sách phòng khám phù hợp để khách chọn phòng khám (nhớ id phòng khám nhưng không nói với khách)
    - hỏi khách chọn dịch vụ và muốn phòng khám nào, ngày khám

3. **check thời gian trống dựa trên phòng khám khách muốn**
    - Sau khi khách chọn xong dùng tool check_slot theo ClinicId(là id theo phòng khám lấy được trả về trước đó nếu không thấy thì gọi 'get_clinics').
    - chỉ hiện giờ trống, không hiện số slot trống.

3.  **Đăng ký và Xác nhận:**
    - Khi người dùng đã chọn được lịch phù hợp.
    - Tóm tắt lại TOÀN BỘ thông tin (Tên, giới tính, năm sinh,SĐT, phòng khám, dịch vụ, thời gian đăng ký).
    - Hỏi người dùng một câu hỏi xác nhận cuối cùng.
    - Chỉ khi người dùng đồng ý, bạn mới được gọi tool `create_booking` để đăng ký lịch.

4.  **Hoàn tất:**
    - Thông báo cho người dùng việc đặt lịch đã thành công, kèm theo mã lịch hẹn (nếu có). 
    - Nhắc tham gia meeting online qua app/web đúng giờ, làm theo bác sĩ để đảm bảo mau khoẻ, động viên.
    - Cảm ơn và chúc người dùng mau khỏe.

**Lưu ý quan trọng:** Luôn tuân thủ nghiêm ngặt quy trình. Không được bỏ bước. Nếu thiếu thông tin, phải hỏi lại.
"""

prompt_template = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ]
)

# --- 4. Quản lý State của Ứng dụng ---
# Dùng dictionary trong bộ nhớ.
# Để dùng trong production, nên thay thế bằng một giải pháp bền bỉ hơn như Redis.
chat_histories: Dict[str, ChatMessageHistory] = {}

def get_session_history(session_id: str) -> ChatMessageHistory:
    """Lấy hoặc tạo mới history cho mỗi session để đảm bảo tính cá nhân hóa."""
    if session_id not in chat_histories:
        chat_histories[session_id] = ChatMessageHistory()
    return chat_histories[session_id]


# --- 5. Xây dựng API với FastAPI ---
app = FastAPI(
    title="API Trợ lý Y khoa An Tâm",
    description="Endpoint để tương tác với chatbot đặt lịch khám bệnh.",
    version="2.0.0"
)
# --- Cấu hình để phục vụ file UI (Front-end) ---
# Dòng này để server có thể truy cập các file tĩnh như CSS, JS nếu cần
# app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    """
    Endpoint này sẽ phục vụ file giao diện chat index.html khi người dùng
    truy cập vào địa chỉ gốc của server (ví dụ: http://localhost:8888).
    """
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
    
@app.on_event("startup")
async def startup_event():
    """
    Khi server khởi động, kết nối đến MCP Server để lấy tool và tạo Agent.
    Sử dụng app.state để lưu agent, tránh dùng biến global.
    """
    logger.info("Đang khởi tạo Agent...")
    try:
        tools = await mcp_client.get_tools()
        if not tools:
            raise RuntimeError("Không lấy được tool nào từ MCP server.")
        
        logger.info(f"Đã lấy thành công {len(tools)} tool(s): {[tool.name for tool in tools]}")
        
        agent = create_tool_calling_agent(llm, tools, prompt_template)
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True, # Giữ True để dễ debug trong quá trình phát triển
            handle_parsing_errors=True
        )
        
        app.state.agent_executor = agent_executor
        logger.info("Agent đã sẵn sàng hoạt động!")

    except Exception as e:
        logger.critical(f"LỖI KHỞI TẠO AGENT KHÔNG THỂ PHỤC HỒI: {e}", exc_info=True)
        app.state.agent_executor = None

# --- Định nghĩa các model Input/Output cho API ---
class ChatInput(BaseModel):
    session_id: str = Field(
        ..., 
        description="ID định danh duy nhất cho mỗi cuộc trò chuyện.",
        example="user123_abc"
    )
    message: str = Field(
        ...,
        description="Nội dung tin nhắn từ người dùng.",
        example="Xin chào, tôi muốn đặt lịch khám."
    )

class ChatResponse(BaseModel):
    response: str

# --- Định nghĩa Endpoint ---
@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(request: Request, chat_input: ChatInput):
    """
    Endpoint chính để trò chuyện với Trợ lý Y khoa An Tâm.
    Mỗi `session_id` sẽ có một luồng hội thoại độc lập.
    """
    agent_executor = request.app.state.agent_executor
    if agent_executor is None:
        raise HTTPException(
            status_code=503, # Service Unavailable
            detail="Agent chưa sẵn sàng. Vui lòng kiểm tra log server để biết chi tiết."
        )

    memory = get_session_history(chat_input.session_id)

    try:
        response = await agent_executor.ainvoke({
            "input": chat_input.message,
            "chat_history": memory.messages
        })
        
        # Cập nhật history sau mỗi lượt chat
        memory.add_user_message(chat_input.message)
        memory.add_ai_message(response["output"])
        
        return {"response": response["output"]}

    except Exception as e:
        logger.error(f"Lỗi trong quá trình xử lý chat session {chat_input.session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Đã có lỗi xảy ra trong quá trình xử lý tin nhắn.")