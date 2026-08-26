import sys, time, base64, io, json, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
from PIL import Image, ImageDraw
import pyautogui

def img_b64(pil_img):
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def ask_local(prompt, b64_img):
    payload = json.dumps({"prompt": f"<image>\n{prompt}", "images": [b64_img], "n_predict": 100}).encode()
    req = urllib.request.Request("http://127.0.0.1:8755/completion", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("content", "").strip()

# Test 1: color
print("-- Test 1: color recognition --")
blue = Image.new("RGB", (64, 64), (30, 60, 220))
t0 = time.time()
print(f"({time.time()-t0:.1f}s): {ask_local('What color is this image? One word.', img_b64(blue))!r}")

# Test 2: text
print("\n-- Test 2: text reading --")
txt = Image.new("RGB", (400, 120), "white")
ImageDraw.Draw(txt).text((20, 40), "HELLO 123", fill="black")
t0 = time.time()
print(f"({time.time()-t0:.1f}s): {ask_local('Read the text in this image.', img_b64(txt))!r}")

# Test 3: agent format with real screen
print("\n-- Test 3: agent format (real screen) --")
shot = pyautogui.screenshot()
b64 = img_b64(shot)
sys_prompt = (
    "You are an Android phone agent controlling an Honor LLY-LX2 (1080x2412). "
    "Respond with EXACTLY ONE action:\nACTION: <action_name> <args>\nREASON: <reason>\n\n"
    "Actions: tap <x> <y>, swipe <x1> <y1> <x2> <y2>, type <text>, press_home, press_back, done <msg>, fail <reason>\n"
    "Shutter button ~(540,2200). Camera switch ~(150,950)."
)
t0 = time.time()
result = ask_local(f"{sys_prompt}\n\nGoal: press_home if you can see a screen", b64)
print(f"({time.time()-t0:.1f}s): {result[:200]!r}")
