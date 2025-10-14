import json
from datetime import datetime
from pathlib import Path

class ConversationLogger:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # Khởi tạo file JSON dạng mảng nếu chưa tồn tại
        if not self.filepath.exists():
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write("[]")

    def log(self, speaker: str, message: str):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "speaker": speaker,
            "message": str(message)
        }

        # Ghi realtime bằng cách mở file, đọc mảng, thêm entry, rồi ghi lại
        try:
            with open(self.filepath, "r+", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = []

                data.append(entry)

                f.seek(0)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.truncate()
        except Exception as e:
            print(f"[LOGGER ERROR]: {e}")

    def log_history(self, history):
        formatted = []
        for entry in history:
            content = entry.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(x) for x in content)

            formatted.append({
                "timestamp": datetime.utcnow().isoformat(),
                "speaker": "user" if entry.get("role") == "user" else "agent",
                "message": content
            })

        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(formatted, f, ensure_ascii=False, indent=2)
