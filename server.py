#!/usr/bin/env python3
"""server.py — Flask backend for Website to APK tool"""

import os, re, uuid, base64, shutil, threading, subprocess
import urllib.request, urllib.parse, urllib.error
import smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from html.parser import HTMLParser
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

JOBS = {}          # in-memory cache
BUILD_DIR = Path("builds")
BUILD_DIR.mkdir(exist_ok=True)

import json as _json_mod

def _job_state_path(job_id):
    return BUILD_DIR / job_id / "state.json"

def _save_job(job_id):
    """Persist job state to disk (minus large binary fields)."""
    job = JOBS.get(job_id)
    if not job: return
    state = {k: v for k, v in job.items() if k not in ("icon","splash")}
    p = _job_state_path(job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "w") as f: _json_mod.dump(state, f)
    except: pass

def _load_job(job_id):
    """Load job state from disk into JOBS cache."""
    p = _job_state_path(job_id)
    if not p.exists(): return None
    try:
        with open(p) as f: state = _json_mod.load(f)
        JOBS[job_id] = state
        return state
    except: return None

def _get_job(job_id):
    """Return job from cache, falling back to disk."""
    return JOBS.get(job_id) or _load_job(job_id)

# ── Email helper ──────────────────────────────────────────────────────────────

SMTP_USER  = os.environ.get("SMTP_USER", "")   # Gmail address
SMTP_PASS  = os.environ.get("SMTP_PASS", "")   # Gmail App Password
SITE_URL   = os.environ.get("SITE_URL", "https://website-to-apk-converter.netlify.app")
API_URL    = os.environ.get("API_URL",  "https://website-to-apk-backend.onrender.com")

def send_apk_email(to_email: str, app_name: str, job_id: str):
    """Send APK-ready notification with download link to the user."""
    if not SMTP_USER or not SMTP_PASS:
        return  # silently skip if not configured
    download_url = f"{API_URL}/api/download/{job_id}"
    subject = f"Your APK is ready — {app_name}"
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#07070f;font-family:'Inter',Arial,sans-serif;color:#e2e8f0">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#07070f;padding:40px 0">
    <tr><td align="center">
      <table width="580" cellpadding="0" cellspacing="0" style="background:#0f0f1a;border-radius:20px;border:1px solid rgba(255,255,255,.08);overflow:hidden">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:32px 40px;text-align:center;border-bottom:1px solid rgba(255,255,255,.06)">
          <span style="font-size:22px;font-weight:900;letter-spacing:-.03em;color:#fff"><span style="color:#7c6dfa">APK</span>forge</span>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:40px">
          <h1 style="margin:0 0 8px;font-size:26px;font-weight:800;color:#fff">Your app is ready! 🎉</h1>
          <p style="margin:0 0 28px;color:#94a3b8;font-size:15px;line-height:1.6">
            <strong style="color:#e2e8f0">{app_name}</strong> has been compiled into an Android APK.<br>
            Click the button below to download it instantly.
          </p>
          <!-- Download button -->
          <table cellpadding="0" cellspacing="0" style="margin:0 0 32px">
            <tr><td style="background:linear-gradient(135deg,#7c6dfa,#9333ea);border-radius:12px;padding:1px">
              <a href="{download_url}" style="display:inline-block;background:linear-gradient(135deg,#7c6dfa,#9333ea);color:#fff;text-decoration:none;font-size:16px;font-weight:700;padding:14px 32px;border-radius:12px;letter-spacing:-.01em">
                ⬇ Download APK
              </a>
            </td></tr>
          </table>
          <!-- Info box -->
          <table width="100%" cellpadding="0" cellspacing="0" style="background:rgba(124,109,250,.08);border:1px solid rgba(124,109,250,.2);border-radius:12px;margin-bottom:28px">
            <tr><td style="padding:20px 24px">
              <p style="margin:0 0 6px;font-size:13px;color:#94a3b8;font-family:monospace;text-transform:uppercase;letter-spacing:.06em">How to install</p>
              <ol style="margin:8px 0 0;padding-left:20px;color:#cbd5e1;font-size:14px;line-height:1.8">
                <li>Download the APK to your Android device</li>
                <li>Open <strong>Settings → Security</strong> and enable <em>Install unknown apps</em></li>
                <li>Tap the downloaded file to install</li>
              </ol>
            </td></tr>
          </table>
          <p style="margin:0;font-size:13px;color:#64748b;line-height:1.6">
            Link expires after 7 days. Need to rebuild? Visit <a href="{SITE_URL}" style="color:#7c6dfa;text-decoration:none">APKforge</a> anytime — it's free.
          </p>
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:20px 40px;border-top:1px solid rgba(255,255,255,.06);text-align:center">
          <p style="margin:0;font-size:12px;color:#475569">
            © 2026 Kindness Community Foundation · Developed by KCF LLC, California USA<br>
            You received this because you built an app at <a href="{SITE_URL}" style="color:#7c6dfa;text-decoration:none">APKforge</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"APKforge <{SMTP_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to_email, msg.as_string())
    except Exception as e:
        print(f"[email] Failed to send to {to_email}: {e}")

# ── HTML scraper ──────────────────────────────────────────────────────────────

class AssetParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.assets = set()
        self.links  = set()
    def _abs(self, href):
        if not href or href.startswith(("data:","javascript:","#","mailto:")):
            return None
        return urllib.parse.urljoin(self.base_url, href)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            url = self._abs(attrs.get("href",""))
            if url and urllib.parse.urlparse(url).netloc == urllib.parse.urlparse(self.base_url).netloc:
                self.links.add(url)
        elif tag in ("script","img","source"):
            url = self._abs(attrs.get("src",""))
            if url: self.assets.add(url)
        elif tag == "link":
            url = self._abs(attrs.get("href",""))
            if url: self.assets.add(url)

def sanitize_filename(url, base_url):
    parsed   = urllib.parse.urlparse(url)
    base_net = urllib.parse.urlparse(base_url).netloc
    path = parsed.path.lstrip("/") or "index.html"
    if parsed.netloc and parsed.netloc != base_net:
        path = os.path.join(parsed.netloc, path)
    if not os.path.splitext(path)[1]:
        path = path.rstrip("/") + "/index.html"
    return path

def download_asset(url, dest):
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest,"wb") as f:
            f.write(r.read())
        return True
    except: return False

def crawl_site(start_url, assets_dir, depth, job_id):
    visited, all_assets = set(), set()
    def emit(msg, pct=None):
        _emit(job_id, msg, pct)
    def crawl(url, d):
        if url in visited or d < 0: return
        visited.add(url)
        emit(f"🌐 Crawling: {url}")
        try:
            req  = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
        except Exception as e:
            emit(f"⚠️  Skip {url}: {e}"); return
        parser = AssetParser(url)
        parser.feed(html)
        rel  = sanitize_filename(url, start_url)
        dest = os.path.join(assets_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        for au in parser.assets:
            ar = sanitize_filename(au, start_url)
            ad = os.path.join(assets_dir, ar)
            all_assets.add((au, ad))
            html = html.replace(au, ar)
        with open(dest,"w",encoding="utf-8") as f: f.write(html)
        for link in parser.links: crawl(link, d-1)
    crawl(start_url, depth)
    emit(f"📦 Downloading {len(all_assets)} assets…", 40)
    ok = 0
    for i, (au, ad) in enumerate(all_assets):
        if download_asset(au, ad): ok += 1
        JOBS[job_id]["progress"] = 40 + int((i/max(len(all_assets),1))*25)
    emit(f"✅ {ok}/{len(all_assets)} assets saved.", 65)

# ── Android templates ─────────────────────────────────────────────────────────

MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{package}" android:versionCode="1" android:versionName="1.0">
    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33"/>
    <uses-permission android:name="android.permission.INTERNET"/>
    <application android:label="{app_name}" android:icon="@drawable/ic_launcher"
        android:allowBackup="true">
        <activity android:name=".MainActivity" android:exported="true"
            android:screenOrientation="{orientation}"
            android:configChanges="orientation|keyboardHidden|screenSize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

MAIN_JAVA = """
package {package};
import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.RelativeLayout;
import android.graphics.Color;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.net.http.SslError;
import android.webkit.SslErrorHandler;
public class MainActivity extends Activity {{
    private WebView wv;
    private ProgressBar progressBar;

    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);

        // Fullscreen — removes URL bar and status bar
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(
            WindowManager.LayoutParams.FLAG_FULLSCREEN,
            WindowManager.LayoutParams.FLAG_FULLSCREEN
        );

        int splashId = getResources().getIdentifier("splash", "drawable", getPackageName());
        if (splashId != 0) {{
            ImageView iv = new ImageView(this);
            iv.setImageResource(splashId);
            iv.setScaleType(ImageView.ScaleType.CENTER_CROP);
            iv.setBackgroundColor(Color.BLACK);
            setContentView(iv);
            new Handler().postDelayed(new Runnable() {{
                public void run() {{ loadSite(); }}
            }}, 2500);
        }} else {{
            loadSite();
        }}
    }}

    private void loadSite() {{
        // Root layout
        RelativeLayout layout = new RelativeLayout(this);
        layout.setBackgroundColor(Color.WHITE);

        // Progress bar at top
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setId(View.generateViewId());
        progressBar.setMax(100);
        progressBar.setProgress(0);
        progressBar.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.parseColor("#7c6dfa")));
        progressBar.setBackgroundColor(Color.TRANSPARENT);
        RelativeLayout.LayoutParams pbParams = new RelativeLayout.LayoutParams(
            RelativeLayout.LayoutParams.MATCH_PARENT, 6);
        pbParams.addRule(RelativeLayout.ALIGN_PARENT_TOP);
        layout.addView(progressBar, pbParams);

        // WebView
        wv = new WebView(this);
        RelativeLayout.LayoutParams wvParams = new RelativeLayout.LayoutParams(
            RelativeLayout.LayoutParams.MATCH_PARENT,
            RelativeLayout.LayoutParams.MATCH_PARENT);
        wvParams.addRule(RelativeLayout.BELOW, progressBar.getId());
        wv.setId(View.generateViewId());
        layout.addView(wv, wvParams);

        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMediaPlaybackRequiresUserGesture(false);

        wv.setWebChromeClient(new WebChromeClient() {{
            @Override
            public void onProgressChanged(WebView view, int progress) {{
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress == 100 ? View.GONE : View.VISIBLE);
            }}
        }});

        wv.setWebViewClient(new WebViewClient() {{
            @Override
            public void onReceivedSslError(WebView v, SslErrorHandler h, SslError e) {{
                h.proceed();
            }}
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {{
                v.loadUrl(url);
                return true;
            }}
            @Override
            public void onPageFinished(WebView view, String url) {{
                progressBar.setVisibility(View.GONE);
            }}
        }});

        setContentView(layout);
        wv.loadUrl("{url}");
    }}

    @Override
    public void onBackPressed() {{
        if (wv != null && wv.canGoBack()) {{
            wv.goBack();
        }} else {{
            super.onBackPressed();
        }}
    }}
}}
"""

def slugify(t): return re.sub(r"[^a-z0-9]","",t.lower()) or "app"

def detect_build_tools():
    ah = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Library/Android/sdk"))
    bt = os.path.join(ah,"build-tools")
    if not os.path.isdir(bt): return None,None,None
    for v in sorted(os.listdir(bt), reverse=True):
        if os.path.exists(os.path.join(bt,v,"aapt")):
            return ah, os.path.join(bt,v), v
    return None,None,None

def detect_platform(ah):
    pd = os.path.join(ah,"platforms")
    if not os.path.isdir(pd): return None
    for p in sorted(os.listdir(pd), reverse=True):
        j = os.path.join(pd,p,"android.jar")
        if os.path.exists(j): return j
    return None

def write(base, rel, content):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,"w",encoding="utf-8") as f: f.write(content)

def build_apk(project_dir, job_id):
    def emit(msg, pct=None):
        _emit(job_id, msg, pct)

    ah, bt, btv = detect_build_tools()
    if not bt: emit("❌ Android build-tools not found."); return None
    aj = detect_platform(ah)
    if not aj: emit("❌ Android platform jar not found."); return None
    emit(f"🔧 Using build-tools {btv}", 70)

    use_d8  = os.path.exists(os.path.join(bt,"d8"))
    dex_cmd = (f'"{bt}/d8" --output . $(find obj/ -name "*.class")'
               if use_d8 else f'"{bt}/dx" --dex --output=classes.dex obj/')
    pkg_path = JOBS[job_id]["package"].replace(".","/")

    build_sh = f"""#!/usr/bin/env bash
set -e
ANDROID_JAR="{aj}"
BUILD_TOOLS="{bt}"
echo "→ Compiling resources…"
"$BUILD_TOOLS/aapt" package -f -m -J gen/ -M AndroidManifest.xml -S res/ -I "$ANDROID_JAR"
echo "→ Compiling Java…"
javac -source 1.8 -target 1.8 -classpath "$ANDROID_JAR" -d obj/ src/{pkg_path}/MainActivity.java gen/{pkg_path}/R.java
echo "→ Dexing…"
{dex_cmd}
echo "→ Packaging APK…"
"$BUILD_TOOLS/aapt" package -f -M AndroidManifest.xml -S res/ -I "$ANDROID_JAR" -F bin/app.unsigned.apk assets/
cd bin && jar uf app.unsigned.apk ../classes.dex && cd ..
echo "→ Signing…"
if [ -f "$BUILD_TOOLS/apksigner" ]; then
    "$BUILD_TOOLS/apksigner" sign --ks bin/debug.keystore --ks-pass pass:android --out bin/app.apk bin/app.unsigned.apk
else
    jarsigner -keystore bin/debug.keystore -storepass android bin/app.unsigned.apk androiddebugkey
    cp bin/app.unsigned.apk bin/app.apk
fi
echo "✅ Done: bin/app.apk"
"""
    with open(os.path.join(project_dir,"build.sh"),"w") as f: f.write(build_sh)
    os.chmod(os.path.join(project_dir,"build.sh"),0o755)

    ks = os.path.join(project_dir,"bin","debug.keystore")
    os.makedirs(os.path.join(project_dir,"bin"), exist_ok=True)
    if not os.path.exists(ks):
        subprocess.run(["keytool","-genkey","-v","-keystore",ks,"-alias","androiddebugkey",
            "-keyalg","RSA","-keysize","2048","-validity","10000",
            "-storepass","android","-keypass","android",
            "-dname","CN=Android Debug,O=Android,C=US"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    emit("🔨 Compiling APK…", 80)
    r = subprocess.run(["bash","build.sh"], cwd=project_dir, capture_output=True, text=True)
    if r.returncode != 0:
        emit(f"❌ Build failed:\n{r.stderr[-800:]}"); return None
    apk = os.path.join(project_dir,"bin","app.apk")
    return apk if os.path.exists(apk) else None

def save_image(b64_data, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path,"wb") as f:
        f.write(base64.b64decode(b64_data))

def generate_kcf_splash() -> bytes | None:
    """Create a KCF-branded 1080×1920 splash screen PNG."""
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont

        W, H = 1080, 1920
        BG       = (7,   7,  15)   # #07070f
        ACCENT   = (124, 109, 250) # #7c6dfa
        BLUE     = (56,  189, 248) # #38bdf8
        MUTED    = (148, 163, 184) # #94a3b8
        SUBTLER  = (71,  85,  105) # #475569

        img  = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # Soft radial glow behind logo — draw layered ellipses
        for r in range(340, 0, -20):
            alpha = int(18 * (1 - r / 340))
            overlay = Image.new("RGB", (W, H), BG)
            od = ImageDraw.Draw(overlay)
            od.ellipse([(W//2 - r, H//2 - r - 60), (W//2 + r, H//2 + r - 60)],
                       fill=(80, 60, 200))
            img = Image.blend(img, overlay, alpha / 255)
            draw = ImageDraw.Draw(img)

        # Try system fonts (Ubuntu 22.04 in Docker)
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        font_reg_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        def load_font(paths, size):
            for p in paths:
                try: return ImageFont.truetype(p, size)
                except: pass
            return ImageFont.load_default()

        f_logo = load_font(font_paths,     200)
        f_sub  = load_font(font_reg_paths,  68)
        f_tag  = load_font(font_reg_paths,  42)

        # ── KCF letters in accent colour ──────────────────────────────────
        logo_txt = "KCF"
        bb = draw.textbbox((0, 0), logo_txt, font=f_logo)
        lw, lh = bb[2] - bb[0], bb[3] - bb[1]
        lx = (W - lw) // 2 - bb[0]
        ly = H // 2 - lh - 50 - bb[1]
        draw.text((lx, ly), logo_txt, fill=ACCENT, font=f_logo)

        # Thin accent underline
        draw.rectangle([(W//2 - 120, ly + lh + 18), (W//2 + 120, ly + lh + 22)],
                       fill=BLUE)

        # ── "App Builder" subtitle ────────────────────────────────────────
        sub = "App Builder"
        sb = draw.textbbox((0, 0), sub, font=f_sub)
        sw = sb[2] - sb[0]
        draw.text(((W - sw) // 2 - sb[0], H // 2 + 36 - sb[1]),
                  sub, fill=MUTED, font=f_sub)

        # ── Bottom tag ────────────────────────────────────────────────────
        tag = "Made with KCF App Builder"
        tb = draw.textbbox((0, 0), tag, font=f_tag)
        tw = tb[2] - tb[0]
        draw.text(((W - tw) // 2 - tb[0], H - 180 - tb[1]),
                  tag, fill=SUBTLER, font=f_tag)

        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[splash] KCF splash generation failed: {e}")
        return None

def _emit(job_id, msg, pct=None):
    job = JOBS[job_id]
    job["log"].append(msg)
    if pct is not None: job["progress"] = pct
    _save_job(job_id)

def run_job(job_id):
    job = JOBS[job_id]
    url, app_name, package, depth = job["url"], job["app_name"], job["package"], job["depth"]
    orientation = job.get("orientation", "unspecified")
    icon_b64, splash_b64 = job.get("icon"), job.get("splash")
    project_dir = str(BUILD_DIR / job_id)
    assets_dir  = os.path.join(project_dir, "_assets")
    try:
        _emit(job_id, f"🚀 Starting build for: {url}", 5)
        crawl_site(url, assets_dir, depth, job_id)

        _emit(job_id, "📁 Generating Android project…", 67)
        pkg_path = package.replace(".","/")

        for d in [f"src/{pkg_path}","res/values","res/drawable","gen","obj","bin","assets"]:
            os.makedirs(os.path.join(project_dir,d), exist_ok=True)

        if os.path.isdir(assets_dir):
            shutil.copytree(assets_dir, os.path.join(project_dir,"assets"), dirs_exist_ok=True)

        write(project_dir, "AndroidManifest.xml", MANIFEST.format(package=package, app_name=app_name, orientation=orientation))
        write(project_dir, f"src/{pkg_path}/MainActivity.java", MAIN_JAVA.format(package=package, url=url))
        write(project_dir, "res/values/strings.xml",
              f'<?xml version="1.0" encoding="utf-8"?><resources><string name="app_name">{app_name}</string></resources>')

        if icon_b64:
            _emit(job_id, "🎨 Adding app icon…")
            save_image(icon_b64, os.path.join(project_dir,"res/drawable","ic_launcher.png"))
        else:
            placeholder = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
            with open(os.path.join(project_dir,"res/drawable","ic_launcher.png"),"wb") as f:
                f.write(placeholder)

        paid = job.get("paid", False)
        splash_dest = os.path.join(project_dir, "res/drawable", "splash.png")

        if splash_b64 and paid:
            # Paid user with custom splash — use as-is, no branding
            _emit(job_id, "🖼️  Adding custom splash screen…")
            save_image(splash_b64, splash_dest)
        elif splash_b64 and not paid:
            # Free user uploaded a splash — overlay KCF watermark on it
            _emit(job_id, "🖼️  Adding splash with KCF branding…")
            try:
                import io as _io
                from PIL import Image, ImageDraw, ImageFont
                raw = base64.b64decode(splash_b64)
                base_img = Image.open(_io.BytesIO(raw)).convert("RGBA")
                base_img = base_img.resize((1080, 1920), Image.LANCZOS)
                # Dark footer bar
                overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                od.rectangle([(0, 1780), (1080, 1920)], fill=(7, 7, 15, 210))
                base_img = Image.alpha_composite(base_img, overlay)
                draw = ImageDraw.Draw(base_img)
                font_paths = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                ]
                font = ImageFont.load_default()
                for p in font_paths:
                    try: font = ImageFont.truetype(p, 44); break
                    except: pass
                tag = "Made with KCF App Builder"
                tb = draw.textbbox((0, 0), tag, font=font)
                tw = tb[2] - tb[0]
                draw.text(((1080 - tw) // 2 - tb[0], 1840 - tb[1]),
                          tag, fill=(124, 109, 250), font=font)
                final = base_img.convert("RGB")
                buf = _io.BytesIO()
                final.save(buf, "PNG")
                with open(splash_dest, "wb") as f: f.write(buf.getvalue())
            except Exception as e:
                _emit(job_id, f"⚠️  Splash overlay failed ({e}), using KCF default…")
                splash_data = generate_kcf_splash()
                if splash_data:
                    with open(splash_dest, "wb") as f: f.write(splash_data)
        else:
            # Free user, no custom splash — generate full KCF branded splash
            _emit(job_id, "🏷️  Adding KCF branded splash screen…")
            splash_data = generate_kcf_splash()
            if splash_data:
                with open(splash_dest, "wb") as f: f.write(splash_data)

        apk = build_apk(project_dir, job_id)
        if apk:
            job["apk_path"] = apk
            job["status"]   = "done"
            job["progress"] = 100
            _emit(job_id, "🎉 APK built successfully!", 100)
            # Send download link email if user provided one
            user_email = job.get("email","")
            if user_email and "@" in user_email:
                _emit(job_id, f"📧 Sending download link to {user_email}…")
                threading.Thread(
                    target=send_apk_email,
                    args=(user_email, app_name, job_id),
                    daemon=True
                ).start()
        else:
            job["status"] = "error"
            if not any("❌" in l for l in job["log"]):
                _emit(job_id, "❌ Build failed: APK not produced. Check Android SDK setup.")
            _save_job(job_id)
    except Exception as e:
        job["status"] = "error"
        _emit(job_id, f"❌ Error: {e}")
        _save_job(job_id)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/site-info")
def site_info():
    raw_url = request.args.get("url","").strip()
    if not raw_url:
        return jsonify({"error":"No URL"}), 400
    if not raw_url.startswith(("http://","https://")):
        raw_url = "https://" + raw_url

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.title=""; self.favicon=""; self.desc=""; self._t=False
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=="title": self._t=True
            elif tag=="link" and ("icon" in a.get("rel","") or a.get("rel")=="shortcut icon"):
                h=a.get("href","")
                if h: self.favicon=urllib.parse.urljoin(raw_url,h)
            elif tag=="meta" and a.get("name")=="description":
                self.desc=a.get("content","")[:200]
        def handle_data(self,d):
            if self._t: self.title+=d; self._t=False
        def handle_endtag(self,tag):
            if tag=="title": self._t=False

    try:
        req=urllib.request.Request(raw_url,headers={"User-Agent":"Mozilla/5.0"})
        html=urllib.request.urlopen(req,timeout=8).read().decode("utf-8",errors="replace")
        p=_P(); p.feed(html)
        parsed=urllib.parse.urlparse(raw_url)
        domain=parsed.netloc.replace("www.","")
        if not p.favicon: p.favicon=f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        return jsonify({"url":raw_url,"title":(p.title.strip()[:60] or domain),
                        "favicon":p.favicon,"description":(p.desc or f"Mobile app for {domain}"),
                        "domain":domain})
    except Exception:
        parsed=urllib.parse.urlparse(raw_url)
        domain=parsed.netloc.replace("www.","") or raw_url
        return jsonify({"url":raw_url,"title":domain,
                        "favicon":f"https://{parsed.netloc}/favicon.ico",
                        "description":f"Mobile app for {domain}","domain":domain})

@app.route("/api/capture-email", methods=["POST"])
def capture_email():
    data=request.json or {}
    email=data.get("email","").strip()
    if not email: return jsonify({"ok":False}), 400
    try:
        with open("leads.txt","a") as f: f.write(f"{email}\t{data.get('url','')}\n")
    except: pass
    return jsonify({"ok":True})

@app.route("/api/contact", methods=["POST"])
def contact():
    data=request.json or {}
    import json as _json, datetime
    entry={
        "ts": datetime.datetime.utcnow().isoformat(),
        "name": data.get("name","").strip(),
        "email": data.get("email","").strip(),
        "country": data.get("country","").strip(),
        "phone": data.get("phone","").strip(),
        "reason": data.get("reason","").strip(),
        "message": data.get("message","").strip()
    }
    try:
        with open("contacts.jsonl","a") as f: f.write(_json.dumps(entry)+"\n")
    except: pass
    # Send confirmation to user + notify admin
    def _send_contact_emails():
        to = entry["email"]
        name = entry["name"] or "there"
        reason = entry["reason"] or "your enquiry"
        if to and "@" in to:
            html_user = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#07070f;font-family:'Inter',Arial,sans-serif;color:#e2e8f0">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#07070f;padding:40px 0">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#0f0f1a;border-radius:20px;border:1px solid rgba(255,255,255,.08)">
<tr><td style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:28px 36px;border-bottom:1px solid rgba(255,255,255,.06);text-align:center">
  <span style="font-size:20px;font-weight:900;color:#fff"><span style="color:#7c6dfa">APK</span>forge</span>
</td></tr>
<tr><td style="padding:36px">
  <h2 style="margin:0 0 12px;font-size:22px;color:#fff">Thanks for reaching out, {name}! 👋</h2>
  <p style="margin:0 0 20px;color:#94a3b8;font-size:15px;line-height:1.7">
    We've received your message about <strong style="color:#e2e8f0">{reason}</strong>.<br>
    Our team will get back to you within <strong style="color:#7c6dfa">24 hours</strong>.
  </p>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:rgba(124,109,250,.08);border:1px solid rgba(124,109,250,.2);border-radius:12px">
  <tr><td style="padding:18px 22px;font-size:13px;color:#94a3b8;line-height:1.7">
    <strong style="color:#cbd5e1">Your message was logged:</strong><br>
    {entry.get('message','') or '(no additional details)'}
  </td></tr></table>
</td></tr>
<tr><td style="padding:18px 36px;border-top:1px solid rgba(255,255,255,.06);text-align:center">
  <p style="margin:0;font-size:12px;color:#475569">© 2026 Kindness Community Foundation · KCF LLC, California USA</p>
</td></tr>
</table></td></tr></table></body></html>"""
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"We got your message — APKforge Support"
            msg["From"]    = f"APKforge Support <{SMTP_USER}>"
            msg["To"]      = to
            msg.attach(MIMEText(html_user, "html"))
            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
                    s.login(SMTP_USER, SMTP_PASS)
                    s.sendmail(SMTP_USER, to, msg.as_string())
            except Exception as e:
                print(f"[email] contact confirm failed: {e}")
        # Admin notification
        if SMTP_USER and SMTP_PASS:
            admin_body = "\n".join(f"{k}: {v}" for k,v in entry.items())
            msg2 = MIMEText(admin_body, "plain")
            msg2["Subject"] = f"[APKforge Contact] {reason} — {entry['name']} <{entry['email']}>"
            msg2["From"]    = f"APKforge <{SMTP_USER}>"
            msg2["To"]      = SMTP_USER
            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
                    s.login(SMTP_USER, SMTP_PASS)
                    s.sendmail(SMTP_USER, SMTP_USER, msg2.as_string())
            except Exception as e:
                print(f"[email] admin notify failed: {e}")
    if SMTP_USER and SMTP_PASS:
        threading.Thread(target=_send_contact_emails, daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/build", methods=["POST"])
def start_build():
    data     = request.json or {}
    url      = data.get("url","").strip()
    app_name = data.get("app_name","") or urllib.parse.urlparse(url).netloc.replace("www.","")
    depth    = int(data.get("depth",1))
    package  = data.get("package","") or f"com.offline.{slugify(app_name.split('.')[0])}"
    raw_orient = data.get("orientation","both")
    orient_map = {"portrait":"portrait","landscape":"landscape","both":"unspecified"}
    orientation = orient_map.get(raw_orient, "unspecified")
    if not url.startswith("http"):
        return jsonify({"error":"Invalid URL"}), 400
    job_id = str(uuid.uuid4())[:8]
    user_email = data.get("email","").strip()
    paid = bool(data.get("paid", False))
    JOBS[job_id] = {"status":"running","progress":0,"log":[],"apk_path":None,
                    "url":url,"app_name":app_name,"package":package,"depth":depth,
                    "orientation":orientation,"email":user_email,"paid":paid,
                    "icon":data.get("icon"),"splash":data.get("splash")}
    _save_job(job_id)   # persist immediately so any worker can find it
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id":job_id})

@app.route("/api/status/<job_id>")
def job_status(job_id):
    job = _get_job(job_id)
    if not job: return jsonify({"error":"Not found"}), 404
    return jsonify({"status":job["status"],"progress":job["progress"],"log":job["log"]})

@app.route("/api/download/<job_id>")
def download_apk(job_id):
    job = _get_job(job_id)
    if not job:
        return jsonify({"error":"Job not found — the server may have restarted. Please rebuild."}), 404
    if job["status"] != "done":
        return jsonify({"error":f"Build status: {job['status']}. APK not ready yet."}), 404
    apk_path = job.get("apk_path")
    # Also check the standard path on disk in case apk_path is relative
    if not apk_path or not os.path.exists(apk_path):
        apk_path = str(BUILD_DIR / job_id / "bin" / "app.apk")
    if not os.path.exists(apk_path):
        return jsonify({"error":"APK file missing — please rebuild."}), 404
    slug = slugify(urllib.parse.urlparse(job["url"]).netloc.replace("www.",""))
    return send_file(apk_path, as_attachment=True,
                     download_name=f"{slug}_offline.apk",
                     mimetype="application/vnd.android.package-archive")

@app.route("/")
def index():
    html_path = os.path.join(os.getcwd(), "website-to-apk.html")
    with open(html_path,"r",encoding="utf-8") as f: content = f.read()
    return content, 200, {"Content-Type":"text/html; charset=utf-8"}

if __name__ == "__main__":
    print("\n  🚀  Website → APK Server  →  http://127.0.0.1:8080\n")
    app.run(host="0.0.0.0", port=8080, debug=False)
