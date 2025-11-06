import jwt, time
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LIVEKIT_API_KEY")
API_SECRET = os.getenv("LIVEKIT_API_SECRET")
from datetime import timedelta

from livekit.api.access_token import AccessToken, SIPGrants


token_obj = AccessToken(API_KEY, API_SECRET) \
    .with_identity("sip‑agent‑identity") \
    .with_kind("sip") \
    .with_sip_grants(SIPGrants(admin=True, call=False)) \
    .with_ttl(timedelta(hours=1))

jwt_token = token_obj.to_jwt()
print("Generated SIP‑admin token:", jwt_token)
