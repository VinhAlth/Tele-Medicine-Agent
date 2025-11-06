import os
import time
import jwt
import requests
from dotenv import load_dotenv

# -------------------------------
# 1️⃣ Load env
# -------------------------------
load_dotenv()

LIVEKIT_URL = os.environ.get("LIVEKIT_URL")        # ví dụ: https://voicebot-i4d7ov6w.livekit.cloud
API_KEY = os.environ.get("LIVEKIT_API_KEY")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("LIVEKIT_API_KEY hoặc LIVEKIT_API_SECRET chưa được set hoặc trống")

# -------------------------------
# 2️⃣ In debug env
# -------------------------------
print("🔹 LIVEKIT_URL:", LIVEKIT_URL)
print("🔹 API_KEY:", API_KEY)
print("🔹 API_SECRET (first 4 chars masked):", "****" + API_SECRET[-4:])

# -------------------------------
# 3️⃣ Tạo JWT token sip.admin
# -------------------------------
def create_admin_token(api_key, api_secret, ttl_sec=3600):
    now = int(time.time())
    payload = {
        "iss": api_key,
        "exp": now + ttl_sec,
        "nbf": now,
        "typ": "management",
        "scope": "sip.admin"
    }
    token = jwt.encode(payload, str(api_secret), algorithm="HS256")
    return token

token = create_admin_token(API_KEY, API_SECRET)
print("✅ Token generated:")
print(token)

# -------------------------------
# 4️⃣ In debug token payload (không phải secret key)
# -------------------------------
decoded_payload = jwt.decode(token, options={"verify_signature": False})
print("🔹 Token payload (decoded, no verification):", decoded_payload)

# -------------------------------
# 5️⃣ Gọi API CreateSIPInboundTrunk
# -------------------------------
payload = {
    "name": "MyInboundTrunk",
    "numbers": ["3800103"],
    "allowed_addresses": ["45.119.84.196/32"],
    "auth_username": "103",
    "auth_password": "123456"
}

url = f"{LIVEKIT_URL}/twirp/livekit.SIP/CreateSIPInboundTrunk"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

print("🔹 Sending request to:", url)
resp = requests.post(url, json=payload, headers=headers)

print("🔹 HTTP status code:", resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    print("✅ Inbound trunk created successfully!")
    print(data)  # JSON sẽ có sip_trunk_id
else:
    print("❌ Error creating trunk:", resp.text)
