"""
Patches Streamlit's static index.html to inject PWA tags into <head>.
Run after every `pip install streamlit` in case the file gets overwritten.
"""
import re
import sys
from pathlib import Path

try:
    import streamlit
    idx = Path(streamlit.__file__).parent / "static" / "index.html"
except Exception as e:
    print(f"Cannot locate Streamlit: {e}", file=sys.stderr)
    sys.exit(1)

html = idx.read_text(encoding="utf-8")

HEAD_INJECT = (
    '<link rel="manifest" href="/manifest.json">'
    '<meta name="theme-color" content="#1f77b4">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="CloudDrive">'
    '<link rel="apple-touch-icon" href="/pwa/icons/icon-192.png">'
)

SW_INJECT = (
    '<script>'
    'if("serviceWorker"in navigator){'
    'navigator.serviceWorker.register("/sw.js",{scope:"/"}).catch(function(){});'
    '}'
    '</script>'
)

if HEAD_INJECT in html:
    print("PWA head tags already present — skipping.")
    sys.exit(0)

html = html.replace("<head>", "<head>" + HEAD_INJECT, 1)
html = html.replace("</body>", SW_INJECT + "</body>", 1)

idx.write_text(html, encoding="utf-8")
print(f"Patched {idx}")
