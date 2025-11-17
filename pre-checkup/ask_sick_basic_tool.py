import os
import asyncio
import json
import aiohttp
from dotenv import load_dotenv

from livekit.agents import Agent, AgentSession, ConversationItemAddedEvent, RunContext, function_tool
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class TeleAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
Bạn là một trợ lý y tế ảo đang hỗ trợ bệnh nhân điền phiếu khám online.
Nhiệm vụ của bạn là thu thập thông tin để gửi cho bác sĩ, bao gồm:

Lý do đi khám

Mô tả quá trình bệnh lý

Triệu chứng hiện tại

Thời gian khởi phát

Bạn phải hỏi từng bước một, chỉ sang câu kế tiếp khi bệnh nhân đã trả lời xong câu trước.
Sau khi có đủ 4 thông tin, hãy gọi tool fill_medical_form để gửi dữ liệu.

🎯 Quy trình hội thoại chi tiết:

1️⃣ Mở đầu – Hỏi lý do đi khám:

“Chào anh/chị 👋 Em là trợ lý y tế ảo của phòng khám.
Anh/chị có thể chia sẻ giúp em lý do hôm nay mình đến khám là gì không ạ?
(Ví dụ: khám sức khỏe định kỳ, thấy mệt, đau ở đâu đó, hay muốn kiểm tra lại tình trạng bệnh cũ?)”

→ Khi bệnh nhân trả lời xong, tùy theo nội dung, dẫn dắt sang bước 2:

2️⃣ Hỏi mô tả quá trình bệnh lý:

“Dạ em hiểu rồi ạ. Vậy anh/chị có thể kể rõ hơn về quá trình bệnh lý được không ạ?
(Ví dụ: tình trạng này đã xuất hiện từ trước chưa, có từng điều trị ở đâu hay dùng thuốc gì không?)”

3️⃣ Hỏi triệu chứng hiện tại:

“Cảm ơn anh/chị đã chia sẻ.
Hiện tại thì anh/chị đang gặp những triệu chứng cụ thể nào ạ?
(Ví dụ: đau đầu, ho, sốt, buồn nôn, mệt mỏi, khó thở...)”

4️⃣ Hỏi thời gian khởi phát:

“Em hiểu rồi ạ. Cho em hỏi thêm là những triệu chứng này bắt đầu từ khi nào vậy anh/chị?
(Ví dụ: mới hôm qua, vài ngày gần đây, hay đã kéo dài vài tuần rồi?)”

🩺 Kết thúc:

“Dạ, em đã ghi nhận đầy đủ thông tin rồi ạ. Em sẽ gửi phiếu khám của anh/chị cho bác sĩ để xem xét ngay nhé.”

→ Sau khi thu đủ dữ liệu, gọi tool fill_medical_form để gửi thông tin bệnh nhân lên hệ thống.

💡 Nguyên tắc hội thoại:

Giọng điệu nhẹ nhàng, thân thiện, giống điều dưỡng nói chuyện thật.

Không hỏi dồn, mỗi lần chỉ hỏi 1 câu chính.

Có thể gợi mở nhẹ nếu bệnh nhân trả lời quá ngắn, nhưng không được hỏi lan man.

Trước mỗi câu hỏi mới, phản hồi đồng cảm hoặc cảm ơn để tạo cảm giác tự nhiên.
                """
        )

    async def on_enter(self):
        print("✅ on_enter() được gọi!", flush=True)
        await asyncio.sleep(0.2)
        greeting_obj = await self.session.generate_reply(
            instructions="Chào hỏi bệnh nhân thân thiện và giải thích vai trò"
        )
        await greeting_obj

    @function_tool()
    async def fill_medical_form(self, context: RunContext, medicalHistory: str) -> dict:
        """
        Tool này nhận thông tin tóm gọm chuẩn y khoa của  3 thông tin sau: Mô tả quá trình bệnh lý, Triệu chứng hiện tại, Thời gian khởi phát 
        và gửi PUT request đến API điền phiếu khám bệnh.
        """
        presctiptionId = "20.1394.5220"
        url = f"https://api-gateway.dev.longvan.vn/clinic-service/callback/encounter-session/{presctiptionId}"
        payload = {
            "medicalHistory": medicalHistory,
            "height": "" ,
            "weight": "" ,
            "temperature": "" ,
            "symptoms": ""  # tạm thời bỏ trống
        }
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=payload) as resp:
                resp_data = await resp.text()
                print(f"✅ PUT {url} | status: {resp.status} | response: {resp_data}", flush=True)
                return {"status": resp.status, "response": resp_data}

    async def on_start(self):
        self._setup_event_listeners()
        return await super().on_start()


async def entrypoint(ctx):
    session = AgentSession(
        turn_detection=MultilingualModel(),
        stt= openai.STT(
            model="gpt-4o-transcribe",  # Hoặc "whisper-1" nếu muốn
            language="vi",                  # Tiếng Việt
    ),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(voice="alloy", model="tts-1"),
        vad=silero.VAD.load(
            min_silence_duration=0.35,
            min_speech_duration=0.12,
            activation_threshold=0.40,
        ),
    )

    # --- chỉ in text, phân biệt user/assistant ---
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        role = event.item.role
        text = getattr(event.item, "text_content", None)
        if text:
            if role == "user":
                print(f"[USER] {text}")
            else:
                print(f"[ASSISTANT] {text}")

    agent = TeleAgent()
    await session.start(room=ctx.room, agent=agent)
    await ctx.connect()


if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
