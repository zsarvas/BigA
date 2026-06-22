"""Captive-portal helpers shared by portal.py and setup_screen.py."""

from __future__ import annotations

PORTAL_IP = "192.168.4.1"
PORTAL_HTTP_URL = f"http://{PORTAL_IP}/"
PORTAL_HOSTNAME = "biga.setup"

# iOS CNA triggers when this exact page is NOT returned.
_APPLE_SUCCESS = (
    "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"
)

# Non-success body — prompts iOS/macOS to open the captive portal sheet.
APPLE_CNA_HTML = (
    "<HTML><HEAD><TITLE>BigA Setup</TITLE></HEAD>"
    f'<BODY>WiFi setup required. <a href="{PORTAL_HTTP_URL}">Continue</a></BODY></HTML>'
)

CAPTIVE_PORTAL_PATHS = (
    "/hotspot-detect.html",          # Apple (iOS / macOS)
    "/library/test/success.html",    # Apple legacy
    "/generate_204",                 # Android / Chrome
    "/gen_204",
    "/connecttest.txt",              # Microsoft Windows
    "/ncsi.txt",
    "/redirect",
    "/success.txt",
    "/canonical.html",               # Apple alternate
)


def wifi_qr_string(ssid: str, password: str) -> str:
    """WIFI: QR payload with required escaping for SSID/password special chars."""

    def esc(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(":", "\\:")
            .replace(",", "\\,")
            .replace('"', '\\"')
        )

    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"
