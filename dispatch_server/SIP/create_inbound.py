import os
import requests
import json
from datetime import timedelta
from dotenv import load_dotenv
from livekit.api.access_token import AccessToken, SIPGrants

# --- Load environment variables from .env file ---
load_dotenv()

# --- Retrieve API key and secret from environment ---
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "https://livekit.longvan.vn")
API_KEY = os.getenv("LIVEKIT_API_KEY")
API_SECRET = os.getenv("LIVEKIT_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in .env file")

# --- Generate SIP admin token ---
token_obj = (
    AccessToken(API_KEY, API_SECRET)
    .with_identity("sip-agent-identity")
    .with_kind("sip")
    .with_sip_grants(SIPGrants(admin=True, call=False))
    .with_ttl(timedelta(hours=1))
)
jwt_token = token_obj.to_jwt()
print("✅ Generated SIP-admin token:", jwt_token)

# --- Dispatch Rule payload ---
trunk_id = 'ST_yPzqQFHC9V6V'  # Update this with your trunk ID

dispatch_payload = {
    "name": "RegistrationDispatchRule",
    "trunk_ids": [trunk_id],
    "rule": {
        "dispatchRuleIndividual": {
            "roomPrefix": "cuoc-goi-vao"  # Không dùng prefix, dùng tên phòng cố định
        }
    },
    "room_config": {
        "agents": [            
            {
                "agent_name": "medical_agent",
                "metadata": "inbound medical dispatch"
            }
            ],  # Không cần chỉ định agent, LiveKit sẽ tạo participant mặc định
    }
}

# --- Send POST request ---
url = f"{LIVEKIT_URL}/twirp/livekit.SIP/CreateSIPDispatchRule"
headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Content-Type": "application/json"
}

response = requests.post(url, headers=headers, data=json.dumps(dispatch_payload))

print(f"🔹 Sending request to: {url}")
print(f"🔹 HTTP status code: {response.status_code}")

if response.status_code == 200:
    print("✅ Dispatch Rule created successfully:")
    print(json.dumps(response.json(), indent=2))
else:
    print(f"❌ Error creating dispatch rule: {response.text}")
