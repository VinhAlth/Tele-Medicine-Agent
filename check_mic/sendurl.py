import requests

url = "https://portal.dev.longvan.vn/dynamic-collection/public/v2/webhook/ai_message"

body1 = {
    "senderName": "bệnh nhân__243jê",
    "senderId": "090292922221",
    "receiveId": "090292933332",
    "receiveName": "Bác sĩ__g4wi",
    "isMessageFromEmployee": False,
    "type": "text",
    "content": "chào anh.",
    "timestamp": "2025-10-28T15:06:22.415868",
    "botId": "68aedccde472aa8afe432664",
    "isMessageInGroup": 0
}

body2 = {
    "senderName": "Bác sĩ__g4weei",
    "senderId": "09029293333",
    "receiveId": "09029292222",
    "receiveName": "bệnh nhân__243j",
    "isMessageFromEmployee": True,
    "type": "text",
    "content": "anh là bác sí",  # 👈 bạn sửa nội dung tin nhắn ở đây
    "timestamp": "2025-10-28T15:06:25.415868",
    "botId": "68aedccde472aa8afe432664",
    "isMessageInGroup": 0
}

for i, body in enumerate([body1, body2], start=1):
    response = requests.post(url, json=body)
    print(f"Gửi body {i}: status={response.status_code}")
    print(response.text)
