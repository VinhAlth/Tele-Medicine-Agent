import os
import requests
import json
from datetime import timedelta
from dotenv import load_dotenv
from livekit.api.access_token import AccessToken, SIPGrants

# --- Load env ---
load_dotenv()
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "https://livekit.longvan.vn")
API_KEY = os.getenv("LIVEKIT_API_KEY")
API_SECRET = os.getenv("LIVEKIT_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in .env file")

# --- Generate SIP-admin token ---
token_obj = AccessToken(API_KEY, API_SECRET) \
    .with_identity("sip-agent-identity") \
    .with_kind("sip") \
    .with_sip_grants(SIPGrants(admin=True, call=False)) \
    .with_ttl(timedelta(hours=1))
jwt_token = token_obj.to_jwt()
print("✅ Generated SIP-admin token:", jwt_token)

# --- Delete old dispatch rule ---
old_rule_id = "SDR_g7Da5zGPqtAk"  # ID rule cũ cần xóa
delete_payload = {"sip_dispatch_rule_id": old_rule_id}

delete_url = f"{LIVEKIT_URL}/twirp/livekit.SIP/DeleteSIPDispatchRule"
headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Content-Type": "application/json"
}

response = requests.post(delete_url, headers=headers, data=json.dumps(delete_payload))
print(f"🔹 Sending request to: {delete_url}")
print(f"🔹 HTTP status code: {response.status_code}")

if response.status_code == 200:
    print("✅ Dispatch Rule deleted successfully:")
    print(json.dumps(response.json(), indent=2))
else:
    print(f"❌ Error deleting dispatch rule: {response.text}")
