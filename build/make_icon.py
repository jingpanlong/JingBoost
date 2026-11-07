"""生成应用图标 app.ico（多尺寸）。"""
import os

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景：圆角方形 + 对角渐变
    grad = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        for_t = y / SIZE
        gd.line([(0, y), (SIZE, y)], fill=lerp((17, 32, 64), (30, 58, 138), for_t) + (255,))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([12, 12, SIZE - 12, SIZE - 12], radius=112, fill=255)
    img.paste(grad, (0, 0), mask)

    # 内侧高光
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-40, -160, SIZE + 40, 200], fill=(56, 189, 248, 55))
    glow = glow.filter(ImageFilter.GaussianBlur(38))
    img.alpha_composite(glow)

    # 闪电
    bolt = [
        (300, 54), (128, 296), (232, 296), (188, 470),
        (386, 226), (274, 226),
    ]
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x, y + 8) for x, y in bolt], fill=(6, 20, 60, 160))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    img.alpha_composite(shadow)

    bolt_img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bolt_img)
    bd.polygon(bolt, fill=(255, 255, 255, 255))
    grad2 = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g2 = ImageDraw.Draw(grad2)
    for y in range(60, 440):
        t = (y - 60) / 380
        g2.line([(0, y), (SIZE, y)], fill=lerp((226, 250, 255), (120, 190, 255), t) + (255,))
    bolt_img = Image.composite(grad2, Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)),
                               bolt_img.split()[3])
    img.alpha_composite(bolt_img)

    out = os.path.join(ROOT, "build", "app.ico")
    img.save(out, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    img.save(os.path.join(ROOT, "build", "app.png"))
    print("icon ->", out)


if __name__ == "__main__":
    make()
