"""给 README 拍三张界面图：首页 / 开盒动画 / 揭晓卡。

为什么不用 `chrome --screenshot`：headless 的 virtual-time 与页面里的
`scroll-behavior: smooth`、异步 fetch、弹入动画叠加时，截图时机不可控
（揭晓态甚至静默不出图）。这里用 Chrome DevTools Protocol：

    导航 → 驱动真实交互 → 等目标元素出现 → 按元素 boundingBox 截图

用的都是**当前项目的真实页面与真实样式**，不含任何手绘示意图。

用法：
    python scripts/shoot_readme.py build    # 生成临时驱动页 web/__shot.html
    python scripts/shoot_readme.py shots    # 拍照（需先启动 web/server.py --offline）
    python scripts/shoot_readme.py clean    # 删除临时驱动页

`shots` 会自动 build → 截图 → clean，产物在 docs/images/。
依赖（仅截图工具用，项目本体仍零依赖）：pillow、websocket-client。
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRC = WEB / "index.html"
DST = WEB / "__shot.html"
OUT = ROOT / "docs" / "images"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9333
BASE = "http://127.0.0.1:8787/__shot.html"

DRIVER = r"""
<script>
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (fn, timeout = 12000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const v = fn();
      if (v) return v;
      await sleep(100);
    }
    return null;
  };

  // 驱动页只负责把页面推进到「已选门店、可开盒」这一可复现起点，
  // 具体要拍哪个状态由 CDP 侧继续操作（更接近真人点击）。
  window.addEventListener("load", async () => {
    await sleep(400);
    const pickup = document.querySelector('.scene-card[data-scene="pickup"]');
    if (pickup) pickup.click();
    const searchBtn = await waitFor(() => document.querySelector("#searchStores"));
    if (searchBtn) searchBtn.click();
    const firstStore = await waitFor(() => document.querySelector("#storeList .store-item"));
    if (firstStore) firstStore.click();
    await sleep(300);
    window.scrollTo({ top: 0, behavior: "instant" });
  });
})();
</script>
"""


def build() -> None:
    html = SRC.read_text(encoding="utf-8")
    if "</body>" not in html:
        raise SystemExit("index.html 缺少 </body>")
    DST.write_text(html.replace("</body>", DRIVER + "</body>"), encoding="utf-8")
    print(f"written: {DST}")


def clean() -> None:
    if DST.exists():
        DST.unlink()
        print(f"removed: {DST}")


class CDP:
    def __init__(self, ws_url: str):
        from websocket import create_connection

        self.ws = create_connection(ws_url, timeout=60)
        self._id = 0

    def send(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.send(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=True,
            userGesture=True,
        )
        return r.get("result", {}).get("value")

    def wait_js(self, expr: str, timeout: float = 30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.js(
                "(function(){try{return !!(" + expr + ");}catch(e){return false;}})()"
            )
            if ok:
                return True
            time.sleep(0.15)
        raise TimeoutError(expr)

    def shot(self, box: dict, path: Path):
        res = self.send(
            "Page.captureScreenshot",
            format="png",
            captureBeyondViewport=True,
            clip={
                "x": box["x"],
                "y": box["y"],
                "width": box["w"],
                "height": box["h"],
                "scale": 1,
            },
        )
        path.write_bytes(base64.b64decode(res["data"]))
        print(f"  saved {path.name} ({box['w']}x{box['h']})")


def box_of(cdp: CDP, selector: str, pad: int = 20) -> dict:
    box = cdp.js(
        """
        (function(){
          const el = document.querySelector(%s);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {x: r.left + window.scrollX, y: r.top + window.scrollY,
                  w: r.width, h: r.height};
        })()
        """
        % json.dumps(selector)
    )
    if not box:
        raise RuntimeError(f"找不到元素 {selector}")
    return {
        "x": max(0, int(box["x"]) - pad),
        "y": max(0, int(box["y"]) - pad),
        "w": int(box["w"]) + pad * 2,
        "h": int(box["h"]) + pad * 2,
    }


def launch() -> subprocess.Popen:
    ud = Path(r"C:\Users\falingwen\AppData\Local\Temp\chrome-shot\cdp")
    shutil.rmtree(ud, ignore_errors=True)
    proc = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*",
            f"--user-data-dir={ud}",
            "--window-size=1280,1500",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1):
                return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("Chrome CDP 未就绪（检查 9333 端口是否被占用）")


def new_tab() -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


def goto(cdp: CDP, url: str):
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send(
        "Emulation.setDeviceMetricsOverride",
        width=1280,
        height=1500,
        deviceScaleFactor=1,
        mobile=False,
    )
    cdp.send("Page.navigate", url=url)


def wait_ready(cdp: CDP):
    wait_js = cdp.wait_js
    wait_js("document.querySelector('#openBtn') && !document.querySelector('#openBtn').disabled")
    wait_js("document.querySelector('#storeList .store-item.active')")
    time.sleep(0.4)


def shoot_home(cdp: CDP, path: Path):
    goto(cdp, f"{BASE}?state=home")
    wait_ready(cdp)
    box = cdp.js(
        """(function(){
             const c = document.querySelector('.container').getBoundingClientRect();
             return {w: document.documentElement.clientWidth,
                     h: Math.ceil(c.bottom + window.scrollY + 28)};
           })()"""
    )
    cdp.shot({"x": 0, "y": 0, "w": int(box["w"]), "h": int(box["h"])}, path)


def opening_box(cdp: CDP, min_w: int = 320) -> dict:
    """开盒阶段的外接框。

    直接量 ``#boxStage``：它已经把礼物盒与「正在开盒…」包在一起，且自带 30px
    上下内边距，正好是不需要再加留白的边界。**不向上扩张**——再往上是遮罩后面
    的页面，会把「可点开一盒…」之类的提示文案裁进画面。宽度补到 ``min_w`` 并
    水平居中，避免 ``.gift-lid`` 的 ``left/right:-8px`` 溢出被裁。
    """
    rect = cdp.js(
        """
        (function(){
          const el = document.querySelector('#boxStage');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {x: r.left + window.scrollX, y: r.top + window.scrollY,
                  w: r.width, h: r.height};
        })()
        """
    )
    if not rect:
        raise RuntimeError("找不到开盒动画容器 #boxStage")
    w, h, x0 = rect["w"], rect["h"], rect["x"]
    if w < min_w:
        x0 -= (min_w - w) / 2
        w = min_w
    return {
        "x": max(0, int(x0)),
        "y": max(0, int(rect["y"])),
        "w": int(w),
        "h": int(h),
    }


def shoot_opening(cdp: CDP, path: Path):
    goto(cdp, f"{BASE}?state=home")
    wait_ready(cdp)
    cdp.js("document.querySelector('#openBtn').click(); true")
    cdp.wait_js("!document.querySelector('#reveal').hidden")
    # 冻结在「盒子摇晃 + 正在开盒…」：这是真实的开盒动画阶段
    cdp.js(
        """(function(){
             const box = document.querySelector('#giftBox');
             if (box) {
               // 点击后盒子上会挂 .opening 触发 lid-open（盖子飞走并淡出）。
               // 这里摘掉它并停掉盖子动画，拍一个完整的盒子 —— 那才是「摇晃中」
               // 的姿态；否则只剩盒身 + 一条悬空的缎带。
               box.classList.remove('opening');
               const lid = box.querySelector('.gift-lid');
               if (lid) lid.style.animation = 'none';
               box.classList.add('shaking');
               // 暂停在确定帧（shake 的 25%：rotate(-6deg) scale(1.02)），
               // 否则量框与截图之间动画还在走，姿态一变就会裁掉边角。
               box.style.animationDelay = '-0.15s';
               box.style.animationPlayState = 'paused';
             }
             const card = document.querySelector('#revealCard');
             if (card) card.hidden = true;
             const t = document.querySelector('#openingText');
             if (t) t.textContent = '正在开盒…';
             return true;
           })()"""
    )
    time.sleep(0.35)
    cdp.shot(opening_box(cdp), path)


def shoot_reveal(cdp: CDP, path: Path):
    goto(cdp, f"{BASE}?state=home")
    wait_ready(cdp)
    # 与 README 顶部 CLI 示例同参数：预算 40 元 + 口味「香辣过瘾」
    cdp.js("document.querySelector('.chip[data-mode=\"budget\"]').click(); true")
    cdp.js("document.querySelector('.chip[data-mode=\"taste\"]').click(); true")
    time.sleep(0.3)
    cdp.js(
        """(function(){
             const b = document.querySelector('#budgetInput');
             if (b) { b.value = '40'; b.dispatchEvent(new Event('input',{bubbles:true})); }
             const s = document.querySelector('.taste-chip[data-taste="辣"]');
             if (s) s.click();
             return true;
           })()"""
    )
    time.sleep(0.3)
    cdp.js("document.querySelector('#openBtn').click(); true")
    cdp.wait_js(
        "!document.querySelector('#revealCard').hidden && "
        "document.querySelectorAll('#revealItems li').length > 0"
    )
    time.sleep(1.6)  # 等揭晓卡 pop-in 收尾
    cdp.shot(box_of(cdp, "#revealCard", pad=18), path)


def shots() -> None:
    from PIL import Image

    OUT.mkdir(parents=True, exist_ok=True)
    build()
    proc = launch()
    try:
        cdp = CDP(new_tab())
        print("截图：")
        shoot_home(cdp, OUT / "ui-home.png")
        shoot_opening(cdp, OUT / "ui-opening.png")
        shoot_reveal(cdp, OUT / "ui-reveal.png")
    finally:
        proc.kill()
        clean()

    # 压到 README 合适的体积：宽 900 对 2x 屏也够清晰
    for name in ("ui-home.png", "ui-opening.png", "ui-reveal.png"):
        p = OUT / name
        if not p.exists():
            continue
        im = Image.open(p)
        if im.width > 900:
            im = im.resize((900, int(im.height * 900 / im.width)), Image.LANCZOS)
        im.convert("RGB").save(p, optimize=True)
        print(f"  optimized {name}: {im.width}x{im.height}, {p.stat().st_size // 1024}KB")

    # 两张小图并排展示，README 里给了同一个 width → 宽高比必须一致。
    # 开盒图按揭晓图归一化：多出来的裁掉（截图时留了 40px 余量），
    # 不够的用上下边缘行往外延伸补齐（单点填纯色会在渐变背景上留下色带）。
    opening, reveal = OUT / "ui-opening.png", OUT / "ui-reveal.png"
    if opening.exists() and reveal.exists():
        im, ref = Image.open(opening).convert("RGB"), Image.open(reveal)
        want = int(round(im.width / (ref.width / ref.height)))
        if want < im.height:
            top = (im.height - want) // 2
            im = im.crop((0, top, im.width, top + want))
        elif want > im.height:
            top = (want - im.height) // 2
            bottom = want - im.height - top
            canvas = Image.new("RGB", (im.width, want))
            if top:
                head = im.crop((0, 0, im.width, 1)).resize((im.width, top), Image.NEAREST)
                canvas.paste(head, (0, 0))
            if bottom:
                tail = im.crop((0, im.height - 1, im.width, im.height)).resize(
                    (im.width, bottom), Image.NEAREST
                )
                canvas.paste(tail, (0, top + im.height))
            canvas.paste(im, (0, top))
            im = canvas
        im.save(opening, optimize=True)
        print(f"  normalized {opening.name}: {im.width}x{im.height}（与揭晓图同宽高比）")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shots"
    {"build": build, "clean": clean, "shots": shots}[cmd]()
