import os
import uuid
import json
from app.conversation_logger import ConversationLogger
from livekit.agents import Agent
from app.mcp_tools.save_user import mcp  # MCP server
import asyncio
from typing import AsyncIterable
from datetime import datetime
import pytz

class AssistantAgent(Agent):
    def __init__(self, prompt_file: str, log_file: str = "chat_log.json"):
        instructions = self._load_prompt(prompt_file)
        super().__init__(instructions=instructions)

        self.mcp = mcp
        self.user_sessions = {}  # user_id -> sessionId

        # --- tạo file log nếu chưa có ---
        self.logger = ConversationLogger(log_file)
        self.current_response = ""  # Buffer để tích lũy output realtime


    def get_current_time(self, tz_name="Asia/Ho_Chi_Minh"):
        tz = pytz.timezone(tz_name)
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    # --- Load prompt và inject current_time ---
    def _load_prompt(self, prompt_file: str) -> str:
        file_path = os.path.join(
            "/root/AGENT/Tele_Medician/prompts", prompt_file
        )
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Không tìm thấy prompt file: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            template = f.read()

        # Inject current time vào placeholder {current_time}
        current_time = self.get_current_time()
        prompt_filled = template.format(current_time=current_time)
        return prompt_filled

    # --- Override transcription_node để print và log output realtime ---
    async def transcription_node(self, text: AsyncIterable[str], model_settings=None) -> AsyncIterable[str]:
        self.current_response = ""
        async for chunk in text:
            self.current_response += chunk
            print(chunk, end='', flush=True)  # Print realtime (không xuống dòng)
            yield chunk
        print()  # Xuống dòng khi hoàn thành
        self.logger.log("agent", self.current_response)  # Log full text khi done
        self.current_response = ""
        
    async def on_user_message(self, message: str, participant=None):
        print(f"🗣️ User said: {message}")
        self.logger.log("user", message)

        # Gửi vào pipeline LLM
        bot_resp_obj = await self.process_input(message)
        await bot_resp_obj  # chờ TTS nói xong
        
            # --- Xử lý input user (text trực tiếp, nếu dùng) ---
    async def process_user_text(self, user_text: str):
        print(f"User: {user_text}")  # Print input realtime
        self.logger.log("user", user_text)  # Log input

        # Process với Agent (output sẽ được handle bởi transcription_node)
        bot_resp_obj = await super().process_input(user_text)
        await bot_resp_obj  # Chờ speech done (nếu cần coordinate)

        return bot_resp_obj  # Trả về object nếu cần

    # --- Khi user vào phiên (greeting sẽ được handle bởi transcription_node) ---
    async def on_enter(self):
        print("✅ on_enter() được gọi!")
        await asyncio.sleep(0.5)

        greeting_obj = await self.session.generate_reply(
            instructions="Bạn là trợ lý y khoa, bạn chuyên đặt lịch khám, hãy chào hỏi khách hàng thân thiện và không quá dài dòng."
        )
        await greeting_obj  # Chờ done (output đã print/log realtime qua node)


        # --- Override process_input để handle khi model trả rỗng ---
    async def process_input(self, message: str, **kwargs):
        """
        Xử lý input người dùng, có timeout & fallback nếu model không trả về gì.
        """
        try:
            # ✅ Giới hạn thời gian xử lý, tránh treo vô hạn
            response_obj = await asyncio.wait_for(
                super().process_input(message, **kwargs),
                timeout=20,  # giây, tùy bạn
            )

            # --- Nếu model trả về mà không có nội dung ---
            if not self.current_response.strip():
                fallback_msg = "Xin lỗi, tôi chưa hiểu ý bạn. Bạn có thể nói lại rõ hơn không?"
                print(f"[⚠️ No response content] {fallback_msg}")
                self.logger.log("agent", fallback_msg)

                if hasattr(self, "session"):
                    reply = await self.session.generate_reply(instructions=fallback_msg)
                    await reply
                    return reply

            return response_obj

        except asyncio.TimeoutError:
            # ✅ Trường hợp LLM không phản hồi trong thời gian cho phép
            print("[⏰ Timeout] Model không phản hồi, chuyển sang fallback.")
            fallback_msg = "Xin lỗi, hệ thống phản hồi chậm. Bạn có thể nói lại giúp tôi không?"
            self.logger.log("agent", fallback_msg)

            if hasattr(self, "session"):
                reply = await self.session.generate_reply(instructions=fallback_msg)
                await reply
                return reply

        except Exception as e:
            # ✅ Trường hợp lỗi khác (network, SDK, etc)
            print(f"[❌ process_input error]: {e}")
            fallback_msg = "Hệ thống đang bận, vui lòng thử lại sau ít phút."
            self.logger.log("agent", fallback_msg)

            if hasattr(self, "session"):
                reply = await self.session.generate_reply(instructions=fallback_msg)
                await reply
                return reply

        return None
    
    

