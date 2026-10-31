# -*- coding: utf-8 -*-
"""生成鸿蒙风格应用图标：蓝色渐变圆角底 + 三层同心弧（涟漪意象）。"""
from PIL import Image, ImageDraw, ImageFilter

S = 512          # 渲染尺寸
SS = 4           # 超采样倍率，保证边缘平滑


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_icon(path: str):
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # ---- 背景：垂直渐变 ----
    top, bot = hex2rgb("#63B8FF"), hex2rgb("#1B5FD9")
    grad = Image.new("RGB", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        t = y / (W - 1)
        gd.line(
            [(0, y), (W, y)],
            fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)),
        )

    # 圆角遮罩
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, W - 1], radius=int(W * 0.225), fill=255)
    img.paste(grad, (0, 0), mask)

    # ---- 顶部高光（模糊柔化，避免硬边）----
    gloss = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        [-int(W * 0.40), -int(W * 0.62), int(W * 1.40), int(W * 0.34)],
        fill=(255, 255, 255, 46),
    )
    gloss = gloss.filter(ImageFilter.GaussianBlur(W * 0.055))
    gloss.putalpha(Image.composite(gloss.getchannel("A"), Image.new("L", (W, W), 0), mask))
    img = Image.alpha_composite(img, gloss)

    # ---- 三层同心弧 ----
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    arcs = [
        # (半径比例, 线宽比例, 起始角, 跨度)
        (0.325, 0.062, -32,  252),
        (0.215, 0.052,  62,  252),
        (0.110, 0.044, 156,  252),
    ]
    for r_ratio, w_ratio, start, extent in arcs:
        r = int(W * r_ratio)
        lw = int(W * w_ratio)
        box = [cx - r, cy - r, cx + r, cy + r]
        d.arc(box, start=start, end=start + extent, fill=(255, 255, 255, 242), width=lw)

    # ---- 中心圆点 ----
    cr = int(W * 0.030)
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(255, 255, 255, 255))

    # ---- 收尾 ----
    img = img.resize((S, S), Image.LANCZOS)

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(path, sizes=sizes)
    img.save(path.replace(".ico", "_preview.png"))
    print("已生成", path)


if __name__ == "__main__":
    make_icon("app_harmony.ico")
