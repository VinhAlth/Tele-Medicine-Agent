import redis, json

r = redis.Redis(
    host='redis-connect.dev.longvan.vn',
    port=32276,
    password='111111aA',
    decode_responses=True
)

hash_key = "room:online"
all_fields = r.hgetall(hash_key)

room_to_topic = {}  # dict để lưu mapping room -> topic

if not all_fields:
    print("❌ Không có room nào trong cache (room:online trống)")
else:
    print(f"✅ Có {len(all_fields)} room trong cache\n")
    for field, value_json in all_fields.items():
        try:
            data = json.loads(value_json)
            room_name = data.get("roomName")
            topic_id = data.get("topicId")
            prescriptionId = data.get("prescriptionId")
            if room_name and topic_id:
                room_to_topic[room_name] = topic_id
            print(f"room={room_name}, topic={topic_id}, prescriptionId={prescriptionId}")
        except Exception as e:
            print(f"Lỗi decode JSON field={field}: {e}")

# Ví dụ: tra cứu topicId từ roomName
room_query = "call_20.178926.8835_20.177629.3219_1762307502536"
topic_id = room_to_topic.get(room_query)
print(f"\nTopicId của room '{room_query}' là: {topic_id}")
