#!/usr/bin/env python3
"""server.py — Flask backend for Website to APK tool"""

import os, re, uuid, base64, shutil, threading, subprocess
import urllib.request, urllib.parse, urllib.error
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

        if splash_b64:
            _emit(job_id, "🖼️  Adding splash screen…")
            save_image(splash_b64, os.path.join(project_dir,"res/drawable","splash.png"))

        apk = build_apk(project_dir, job_id)
        if apk:
            job["apk_path"] = apk
            job["status"]   = "done"
            job["progress"] = 100
            _emit(job_id, "🎉 APK built successfully!", 100)
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
    JOBS[job_id] = {"status":"running","progress":0,"log":[],"apk_path":None,
                    "url":url,"app_name":app_name,"package":package,"depth":depth,
                    "orientation":orientation,
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
