import hashlib
import hmac
import os
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, max_age_seconds: int = 86400):
    if not init_data:
        raise ValueError("Missing Telegram initData")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing Telegram hash")
    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        raise ValueError("Expired Telegram initData")
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Invalid Telegram initData")
    return pairs
