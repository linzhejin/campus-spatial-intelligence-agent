# -*- coding: utf-8 -*-
"""
生成漫步珞珈 PWA 应用图标（PNG，多尺寸）。

设计：满版樱花粉渐变背景（保证 Android maskable 安全区）+ 居中白色五瓣樱花 +
金色花蕊。iOS Safari 主屏图标需要 PNG（不支持 SVG），故必须产出 PNG。

输出到 static/icons/：
  icon-512.png   / icon-192.png   （Android / manifest，any + maskable）
  icon-1024.png  （源尺寸，备用）
  apple-touch-icon.png（180x180，iOS 主屏）
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "icons")

# 品牌色（珞珈秋色 · 樱花）
TOP_BG = (158, 58, 75)       # 深樱红 #9E3A4B
BOTTOM_BG = (232, 146, 156)  # 樱花粉 #E8929C
PETAL = (255, 250, 248)      # 花瓣近白
PETAL_EDGE = (250, 228, 230)
CENTER = (247, 200, 94)      # 花蕊金 #F7C85E
STAMEN = (214, 158, 60)      # 花蕊点深金


def _vertical_gradient(size, top, bottom):
    """生成满版垂直渐变图。"""
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def _petal_layer(S):
    """画一片"朝上"的花瓣（长轴沿 y 轴），返回透明层。"""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = cy = S / 2
    R = S * 0.155          # 花瓣中心到花心距离
    a = S * 0.175          # 花瓣径向半轴（长）
    b = S * 0.120          # 花瓣切向半轴（宽）
    pc_y = cy - R          # 花瓣中心位置（朝上）
    bbox = [cx - b, pc_y - a, cx + b, pc_y + a]
    d.ellipse(bbox, fill=PETAL, outline=PETAL_EDGE, width=max(3, S // 200))
    # 花瓣顶端做一个浅缺口（樱花花瓣特征）：用背景色小圆盖在最外尖端
    notch_r = b * 0.55
    d.ellipse([cx - notch_r, pc_y - a - notch_r * 0.35,
               cx + notch_r, pc_y - a + notch_r * 1.2],
              fill=TOP_BG)
    return layer


def draw_blossom(size):
    """满版樱花粉渐变 + 居中白色五瓣樱花 + 金色花蕊。"""
    S = size * 4  # 超采样抗锯齿
    img = _vertical_gradient(S, TOP_BG, BOTTOM_BG).convert("RGBA")
    cx = cy = S / 2

    base_petal = _petal_layer(S)

    # 投影层（花整体下移一点，模糊深色）
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    for i in range(5):
        shadow = Image.alpha_composite(shadow, base_petal.rotate(i * 72, center=(cx, cy)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(S * 0.03))
    shadow_flat = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh = shadow.split()
    # 把花瓣的 alpha 转成深色
    sh_col = Image.new("RGBA", (S, S), (90, 22, 32, 255))
    shadow_col = Image.composite(sh_col, Image.new("RGBA", (S, S), (0, 0, 0, 0)), sh[3])
    shadow_col = shadow_col.filter(ImageFilter.GaussianBlur(S * 0.02))
    off = int(S * 0.012)
    shadow_bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow_bg.paste(shadow_col, (0, off), shadow_col)
    img = Image.alpha_composite(img, shadow_bg)

    # 五片花瓣（每片旋转 72°）
    flower = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    for i in range(5):
        flower = Image.alpha_composite(flower, base_petal.rotate(i * 72, center=(cx, cy)))

    # 花蕊
    fd = ImageDraw.Draw(flower)
    center_r = S * 0.088
    fd.ellipse([cx - center_r, cy - center_r, cx + center_r, cy + center_r], fill=CENTER)
    for i in range(10):
        ang = math.radians(i * 36)
        rr = center_r * 1.45
        sx, sy = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        sr = S * 0.014
        fd.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=STAMEN)

    img = Image.alpha_composite(img, flower)
    return img.convert("RGB").resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    targets = {
        "icon-1024.png": 1024,
        "icon-512.png": 512,
        "icon-192.png": 192,
        "apple-touch-icon.png": 180,
    }
    # 1024 主绘一次，其余从其缩放，保证一致
    master = draw_blossom(1024)
    master.save(os.path.join(OUT_DIR, "icon-1024.png"))
    master.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT_DIR, "icon-512.png"))
    master.resize((192, 192), Image.LANCZOS).save(os.path.join(OUT_DIR, "icon-192.png"))
    master.resize((180, 180), Image.LANCZOS).save(os.path.join(OUT_DIR, "apple-touch-icon.png"))
    print("已生成图标到", os.path.abspath(OUT_DIR))
    for name in targets:
        p = os.path.join(OUT_DIR, name)
        print("  ", name, os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
