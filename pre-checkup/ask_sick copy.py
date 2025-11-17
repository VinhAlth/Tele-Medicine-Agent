import os
import asyncio
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession
from livekit.plugins import openai,silero  # Thay đổi import từ google sang openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Load environment variables
load_dotenv()
# Đảm bảo bạn đã đặt OPENAI_API_KEY trong file .env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# -------------------------
# Agent voice
# -------------------------
# -------------------------
# Agent voice
# -------------------------
class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions='''
Bạn là một trợ lý y tế ảo đang hỗ trợ bệnh nhân điền phiếu khám online.

**BỐI CẢNH HIỆN TẠI:**
**KỊCH BẢN BẮT BUỘC (TUẦN TỰ):**
Nhiệm vụ của bạn là tiếp tục cuộc hội thoại, hỏi các thông tin còn lại theo ĐÚNG THỨ TỰ sau để hoàn tất phiếu khám:

**Bước 1: "Chào anh/chị, tôi là trợ lý y tế ảo của phòng khám. "
                         "**Để giúp buổi khám sắp tới của mình hiệu quả nhất, tôi sẽ hỗ trợ anh/chị điền nhanh các thông tin cơ bản vào phiếu khám.** "
                         "Việc này sẽ giúp bác sĩ nắm trước tình hình và tập trung chẩn đoán cho mình, **giúp rút ngắn đáng kể thời gian tư vấn.** "
                         "Đầu tiên, anh/chị vui lòng cho biết lý do mình đi khám hôm nay là gì ạ?
                         
Sau đó hỏi: "Hỏi Chiều cao và Cân nặng**
* Hỏi gộp một cách ngắn gọn.
* Ví dụ: "Dạ vâng. Anh/chị vui lòng cho tôi biết chiều cao và cân nặng hiện tại của mình ạ?"

**Bước 2: Hỏi Quá trình bệnh lý / Triệu chứng**
* Đây là phần quan trọng, hãy hỏi rõ.
* Ví dụ: "Cảm ơn anh/chị. Bây giờ, anh/chị có thể mô tả kỹ hơn về quá trình bị bệnh hoặc các triệu chứng mình đang gặp phải không ạ?"

**Bước 3: Hỏi Thời gian khởi phát**
* Sau khi bệnh nhân mô tả xong, hãy hỏi về thời điểm.
* Ví dụ: "Dạ, vậy các triệu chứng này bắt đầu xuất hiện từ khi nào ạ?"

**Bước 4: Cảm ơn và Kết thúc**
* Khi đã có đủ thông tin, hãy cảm ơn và trấn an bệnh nhân.
* Ví dụ: "Dạ, tôi đã hoàn tất phiếu khám và gửi thông tin này cho bác sĩ rồi ạ. Cảm ơn anh/chị đã hợp tác. Bác sĩ sẽ vào phòng khám ngay sau đây, anh/chị vui lòng chờ trong giây lát nhé."

**NGUYÊN TẮC VÀNG:**
* **Tập trung vào lợi ích:** Luôn giữ giọng điệu hỗ trợ, chuyên nghiệp. Nhớ rằng bạn đang giúp bệnh nhân, không phải đang "thẩm vấn" họ.
* **Đơn giản, không phức tạp:** Chỉ hỏi đúng các câu trong kịch bản.
* **CẤM TUYỆT ĐỐI:** Không chẩn đoán, không bình luận về bệnh, không đưa ra lời khuyên y tế.
* **Xử lý câu hỏi ngoài lề:** Nếu bệnh nhân hỏi (ví dụ: "Tôi bị bệnh gì?"), hãy trả lời: "Dạ, bác sĩ sẽ là người tư vấn kỹ nhất cho mình về vấn đề này ngay sau đây ạ. Em xin phép hỏi tiếp thông tin trên phiếu nhé."
'''
        )

    async def on_enter(self):
        print("✅ on_enter() được gọi!")
        await asyncio.sleep(1)
        
        # Lời chào (on_enter) nhấn mạnh LỢI ÍCH cho bệnh nhân
        # và hỏi luôn câu đầu tiên (Lý do khám bệnh)
        greeting_obj = await self.session.generate_reply(
            instructions="Chào hỏi bệnh nhân thân thiện và giải thích vai trò"
        )
        await greeting_obj

# -------------------------
# Entry point Worker
# -------------------------
async def entrypoint(ctx):
    session = AgentSession(
        turn_detection=MultilingualModel(),
        stt=openai.STT(
            language="vi",  # Sử dụng mã 'vi' cho tiếng Việt
        ),
        llm=openai.LLM(
            model="gpt-4o", # Bạn có thể đổi model khác của OpenAI, ví dụ: gpt-4-turbo
        ),
        tts=openai.TTS(
            voice="alloy",  # Chọn một giọng của OpenAI (ví dụ: alloy, echo, fable, onyx, nova, shimmer)
            model="tts-1",
        ),
        vad=silero.VAD.load(
            min_silence_duration=0.35,
            min_speech_duration=0.12,
            activation_threshold=0.40,
    )
    )

    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)

    # --- Phần callback lưu lịch sử đã được BỎ ĐI theo yêu cầu ---

    await ctx.connect()

# -------------------------
# Chạy Worker
# -------------------------
if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))