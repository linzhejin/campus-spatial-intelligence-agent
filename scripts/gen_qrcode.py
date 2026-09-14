"""生成珞珈智行访问二维码。

用途：域名备案完成前，微信会拦截未备案 IP 链接。
生成二维码后，用户可用手机系统浏览器扫码访问，绕过微信内置浏览器拦截。
"""
import qrcode
from pathlib import Path

URL = "http://152.136.102.172:5000"
OUT = Path(__file__).parent.parent / "static" / "qrcode_access.png"

qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    box_size=10,
    border=2,
)
qr.add_data(URL)
qr.make(fit=True)

img = qr.make_image(fill_color="#1f3a5f", back_color="white")
img.save(OUT)
print(f"已生成: {OUT}  ({OUT.stat().st_size} bytes)")
