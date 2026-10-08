"""Build frontend/index.html from src/app.html: adds the page head, Subresource Integrity
for Cesium, and a Content Security Policy that allows inline scripts only by SHA-256 hash.

    python tools/build_index.py src/app.html frontend/index.html
"""
import base64, hashlib, re, sys
src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()
SRI_JS  = "sha384-D1oR8FyBDsJWkPeydGJTh8nJg5/++9sqchzJuu+oGQPmgbwu3aJmoVj3BTowr6t6"
SRI_CSS = "sha384-ghEeMdcWWzRv/BPeUcX835vcKDGrxvROXisl/Btpv3GeekBUXTSPVcFJpI1Tcrgp"
s = s.replace('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Widgets/widgets.css">',
              f'<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Widgets/widgets.css" integrity="{SRI_CSS}" crossorigin="anonymous">')
s = s.replace('<script src="https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Cesium.js"></script>',
              f'<script src="https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Cesium.js" integrity="{SRI_JS}" crossorigin="anonymous"></script>')
assert s.count('integrity=') == 2
# hash every inline <script> so the CSP can forbid all other inline code
hashes = []
for m in re.finditer(r'<script>(.*?)</script>', s, re.S):
    hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() + "'")
csp = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-eval' 'wasm-unsafe-eval' " + " ".join(hashes),
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' data: blob: https://cdn.jsdelivr.net https://api.cesium.com https://*.cesium.com https://tile.googleapis.com https://*.googleapis.com https://*.execute-api.ap-south-1.amazonaws.com https://photon.komoot.io",
    "worker-src 'self' blob: https://cdn.jsdelivr.net",
    "child-src 'self' blob:",
    "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-src 'none'", "manifest-src 'self'",
])
icon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='22' fill='%23ffbe2e'/%3E%3C/svg%3E"
head = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="description" content="SunSense: how much rooftop solar fits your home anywhere in India, what it costs after the PM Surya Ghar subsidy, and when it pays for itself.">
<meta name="theme-color" content="#02040b">
<link rel="icon" href="{icon}">
<style>body{{margin:0}}[hidden]{{display:none!important}}</style>
</head>
<body>
'''
open(out, "w", encoding="utf-8", newline="\n").write(head + s + "\n</body>\n</html>\n")
print(len(hashes), "inline scripts hashed")
