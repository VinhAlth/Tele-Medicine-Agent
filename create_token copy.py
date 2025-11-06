import os
from datetime import timedelta
import requests
from dotenv import load_dotenv
from livekit.api.access_token import AccessToken, SIPGrants

# -------------------------------
# 1️⃣ Load env
# -------------------------------
load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL")        # ví dụ: https://livekit.longvan.vn
API_KEY = os.getenv("LIVEKIT_API_KEY")
API_SECRET = os.getenv("LIVEKIT_API_SECRET")

if not API_KEY or not API_SECRET or not LIVEKIT_URL:
    raise ValueError("LIVEKIT_URL, API_KEY hoặc API_SECRET chưa set")

# -------------------------------
# 2️⃣ Tạo SIP-admin token
# -------------------------------
token_obj = AccessToken(API_KEY, API_SECRET) \
    .with_identity("sip-agent-identity") \
    .with_kind("sip") \
    .with_sip_grants(SIPGrants(admin=True, call=False)) \
    .with_ttl(timedelta(hours=1))

jwt_token = token_obj.to_jwt()
print("✅ Generated SIP-admin token:\n", jwt_token)

headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Content-Type": "application/json"
}

# -------------------------------
# 3️⃣ Tạo SIP Inbound Trunk
# -------------------------------
payload_create = {
    "name": "My trunk",
    "numbers": ["+3800103"]
}


url_create = f"{LIVEKIT_URL}/twirp/livekit.SIP/CreateSIPInboundTrunk"
resp_create = requests.post(url_create, json=payload_create, headers=headers)

if resp_create.status_code == 200:
    data_create = resp_create.json()
    print("✅ Inbound trunk created successfully!")
    print(data_create)
else:
    print("❌ Error creating trunk:", resp_create.status_code, resp_create.text)

# -------------------------------
# 4️⃣ Lấy danh sách SIP Inbound Trunks
# -------------------------------
url_list = f"{LIVEKIT_URL}/twirp/livekit.SIP/ListSIPInboundTrunk"
resp_list = requests.post(url_list, json={}, headers=headers)

if resp_list.status_code == 200:
    print("✅ ListSIPInboundTrunk response:")
    print(resp_list.json())
else:
    print("❌ Error listing trunks:", resp_list.status_code, resp_list.text)
