"""Notify Me's fixed HTTPS endpoint; never log its access code or response body."""

import requests


class AlexaSender:
    def __init__(self, code_file):
        self.code = code_file.read_text("utf-8-sig").strip()
        if (
            not self.code
            or len(self.code) > 8192
            or any(c.isspace() for c in self.code)
            or self.code == "PASTE_YOUR_ALEXA_ACCESS_CODE_HERE"
        ):
            raise ValueError("Alexa access code file must contain a single access code")

    def send(self, title, body):
        result = {"channel": "alexa", "status": "failed"}
        try:
            with requests.post(
                "https://api.notifymyecho.com/v1/NotifyMe",
                json={"accessCode": self.code, "title": title, "notification": body},
                timeout=(5, 15),
                allow_redirects=False,
            ) as response:
                if 200 <= response.status_code < 300:
                    return {"channel": "alexa", "status": "accepted"}
                result["error"] = (
                    "alexa_rate_limited" if response.status_code == 429 else "alexa_rejected"
                )
                result["http_status"] = response.status_code
        except requests.Timeout:
            result["error"] = "alexa_timeout"
        except requests.RequestException:
            result["error"] = "alexa_network_error"
        return result
