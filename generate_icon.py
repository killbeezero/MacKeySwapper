"""
generate_icon.py
從 icon.png 產生 Windows 多尺寸 AppIcon（icon.ico）
包含 16、32、48、64、128、256 px 各尺寸

用法：
    python generate_icon.py
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("❌ 需要 Pillow 套件，請先執行：pip install Pillow")
    sys.exit(1)

# 來源與目標路徑（與此腳本同目錄）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE_DIR, "icon.png")
DST = os.path.join(BASE_DIR, "icon.ico")

# Windows AppIcon 標準尺寸
SIZES = [16, 32, 48, 64, 128, 256]


def generate():
    if not os.path.exists(SRC):
        print(f"❌ 找不到來源檔案：{SRC}")
        sys.exit(1)

    print(f"📂 來源：{SRC}")
    img = Image.open(SRC).convert("RGBA")
    print(f"   原始尺寸：{img.size[0]}x{img.size[1]} px，模式：{img.mode}")

    # 確保來源為最大尺寸（256x256），讓 Pillow 從此縮放各尺寸
    # 注意：ICO 格式不支援 append_images，必須用 sizes 參數讓 Pillow 自動處理
    img_base = img.resize((256, 256), Image.LANCZOS)

    img_base.save(
        DST,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
    )

    for size in SIZES:
        print(f"   ✔ 產生 {size}x{size}")

    size_kb = os.path.getsize(DST) / 1024
    print(f"\n✅ 已產生：{DST}")
    print(f"   包含尺寸：{SIZES}")
    print(f"   檔案大小：{size_kb:.1f} KB")


if __name__ == "__main__":
    generate()
