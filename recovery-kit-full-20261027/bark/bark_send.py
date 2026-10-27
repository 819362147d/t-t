#!/usr/bin/env python3
"""Send a push notification via Bark (iPhone).

The Bark device key lives in the Secure Vault under connection
``custom.bark-push`` and never appears in code, logs, or files.
It is injected at request time through a surrogate exchange, which only
works on egress to the registered host (api.day.app).

Usage: bark_send.py "title" "body"
Prints the Bark API JSON response.
"""
import json
import sys
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import (  # noqa: E402
    dynamic_credential_entry,
    read_json_response,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def bark_url():
    # 注意：不要用 url_with_surrogate_path_segment()，它会对 surrogate 做
    # percent-encoding（hsurr: -> hsurr%3A），Sentinel 就认不出字面 hsurr:
    # 前缀、不做凭证替换，Bark 会返回 400。surrogate 只含 [a-z0-9:]，
    # 在 path 里不编码是安全的。
    entry = dynamic_credential_entry("custom.bark-push")
    surr = str(entry["surrogate"]).strip()
    assert surr.startswith("hsurr:"), "unexpected surrogate format"
    return "https://api.day.app/{}".replace("{}", surr, 1)


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Muse"
    body = sys.argv[2] if len(sys.argv) > 2 else ""
    url = bark_url()
    payload = json.dumps(
        {"title": title, "body": body, "level": "timeSensitive"}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as resp:
        print(json.dumps(read_json_response(resp), ensure_ascii=False))


if __name__ == "__main__":
    main()
