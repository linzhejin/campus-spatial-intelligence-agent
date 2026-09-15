package online.whuspati.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.hardware.GeomagneticField;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.text.TextUtils;
import android.util.Log;
import android.view.Surface;
import android.view.WindowManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.speech.tts.TextToSpeech;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Locale;

/**
 * 珞珈智行 Android 壳应用：全屏 WebView 加载 https://whuspati.online/
 * 无任何第三方依赖（纯 android.* API），手工构建链编译。
 *
 * JS 桥（@JavascriptInterface，网页侧约定）：
 *   WhuWalkerTts.speak(text)/stop()/setMuted(bool)  — Android 原生语音播报
 *   WhuWalkerVoice.start(lang)/stop()               — Android 原生语音识别，
 *       结果经 window.__whuWalkerVoiceCallback(json) 回传
 *   WhuWalkerScreen.setKeepScreenOn(bool)           — 导航中屏幕常亮
 *   WhuWalkerLocation.start()/stop()               — 原生定位+罗盘融合：
 *       window.__whuWalkerLocationCallback(json) 回传
 *       {lat,lng,heading,accuracy,speed,src,ts}（WGS-84，heading 0北90东）
 *
 * 应用内更新（原生，无 JS 桥）：启动数秒后拉取 /app/latest.json，
 * versionCode 更高时弹窗 → 后台下载 APK（SHA-256 校验）→ 授权安装未知来源
 * → 经 ApkFileProvider 拉起系统安装器，用户点一次确认即可覆盖安装。
 */
public class MainActivity extends Activity {

    private static final int LOC_PERM_REQ = 1;
    private static final int RECORD_PERM_REQ = 2;
    private static final int ASR_INTENT_REQ = 3;
    private static final String TAG = "WhuWalker";

    private static final String UPDATE_MANIFEST_URL =
            "https://whuspati.online/app/latest.json";
    private static final String UPDATE_APK_NAME = "whu-walker-update.apk";
    private static final String PREFS_NAME = "whu_prefs";
    private static final long UPDATE_SKIP_WINDOW_MS = 24L * 60 * 60 * 1000; // "以后再说"24h 内不打扰

    private WebView webView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private TextToSpeech tts;
    private boolean ttsMuted = false;
    private boolean ttsReady = false;        // init 完成且语言可用前，speak 只会被丢弃
    private String pendingSpeak = null;     // init 完成前最后一条待播指令
    private int ttsErrorCount = 0;
    private boolean volumeWarned = false;   // 媒体音量为 0 的提示每次运行只发一次
    private SpeechRecognizer recognizer;
    private boolean pendingVoiceStart = false;  // 等麦克风授权后自动开识别
    private boolean voiceFallbackUsed = false;  // 本轮会话是否已降级过系统语音面板，防循环
    private boolean voiceIntentActive = false;  // 正在使用 ACTION_RECOGNIZE_SPEECH 面板

    // 应用内更新状态
    private boolean updatePromptShown = false;          // 一次运行最多弹一次
    private volatile boolean downloadCancelled = false;
    private File pendingInstallApk = null;              // 等"未知来源"授权返回后继续安装
    private AlertDialog downloadDialog;
    private ProgressBar downloadBar;
    private TextView downloadPct;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Android 6.0+ 定位权限必须运行时申请；未授予也不阻塞页面（网页自己有降级提示）
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
            }, LOC_PERM_REQ);
        }

        // 原生 TTS：init 回调里设普通话；未 ready 时的播报进入 pending，ready 后补播
        tts = new TextToSpeech(getApplicationContext(), new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                if (status == TextToSpeech.SUCCESS) {
                    int r = tts.setLanguage(Locale.SIMPLIFIED_CHINESE);
                    if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
                        r = tts.setLanguage(Locale.CHINA);
                    }
                    if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
                        r = tts.setLanguage(Locale.getDefault());
                    }
                    tts.setSpeechRate(1.0f);
                    tts.setPitch(1.0f);
                    if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
                        Log.w(TAG, "TTS no Chinese voice data: " + r);
                        emitTtsStatus("unavailable");
                        return;
                    }
                    attachTtsProgressListener();
                    ttsReady = true;
                    if (pendingSpeak != null) {
                        String t = pendingSpeak;
                        pendingSpeak = null;
                        speakInternal(t);
                    }
                } else {
                    Log.w(TAG, "TTS init failed: " + status);
                    emitTtsStatus("unavailable");
                }
            }
        });

        webView = new WebView(this);
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // localStorage：定位缓存 / PWA 状态
        s.setGeolocationEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        // UA 追加标识：网页据此抑制"安装到主屏幕"横幅（已在 App 内无需再装）
        s.setUserAgentString(s.getUserAgentString() + " WHUWalkerApp/1.3");

        webView.addJavascriptInterface(new TtsBridge(), "WhuWalkerTts");
        webView.addJavascriptInterface(new VoiceBridge(), "WhuWalkerVoice");
        webView.addJavascriptInterface(new ScreenBridge(), "WhuWalkerScreen");
        webView.addJavascriptInterface(new LocationBridge(), "WhuWalkerLocation");

        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin,
                                                           GeolocationPermissions.Callback callback) {
                // 系统权限已在启动时申请；网页源一律放行
                callback.invoke(origin, true, false);
            }
        });

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl("https://whuspati.online/");
        }

        // 启动 4 秒后静默检查应用更新（不抢占首屏；失败完全无感）
        mainHandler.postDelayed(new Runnable() {
            @Override
            public void run() {
                checkForAppUpdate();
            }
        }, 4000);
    }

    // ============ 应用内更新 ============

    private static class UpdateInfo {
        int versionCode;
        String versionName;
        String apkUrl;
        String sha256;
        String changelogText;
    }

    @SuppressWarnings("deprecation")
    private int currentVersionCode() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    private boolean isUpdateSkipped(int versionCode) {
        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        return sp.getInt("skip_code", 0) == versionCode
                && System.currentTimeMillis() < sp.getLong("skip_until", 0);
    }

    private void rememberSkip(int versionCode) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                .putInt("skip_code", versionCode)
                .putLong("skip_until", System.currentTimeMillis() + UPDATE_SKIP_WINDOW_MS)
                .apply();
    }

    /** 后台线程：拉取版本清单，发现新版则回主线程弹窗。任何失败都静默忽略。 */
    private void checkForAppUpdate() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(UPDATE_MANIFEST_URL + "?t=" + System.currentTimeMillis());
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(8000);
                    conn.setReadTimeout(8000);
                    conn.setUseCaches(false);
                    conn.setRequestProperty("Cache-Control", "no-cache");
                    if (conn.getResponseCode() != 200) return;

                    String body;
                    InputStream is = conn.getInputStream();
                    try {
                        byte[] buf = readAllBytes(is);
                        body = new String(buf, "UTF-8");
                    } finally {
                        is.close();
                    }

                    JSONObject j = new JSONObject(body);
                    final UpdateInfo info = parseUpdateInfo(j);
                    if (info == null || info.versionCode <= currentVersionCode()) return;
                    if (isUpdateSkipped(info.versionCode)) return;

                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            showUpdateDialog(info);
                        }
                    });
                } catch (Exception e) {
                    Log.w(TAG, "update check failed: " + e.getMessage());
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        }).start();
    }

    private UpdateInfo parseUpdateInfo(JSONObject j) {
        UpdateInfo info = new UpdateInfo();
        info.versionCode = j.optInt("versionCode", 0);
        info.versionName = j.optString("versionName", "");
        info.apkUrl = j.optString("apkUrl", "");
        info.sha256 = j.optString("sha256", "").trim().toLowerCase(Locale.ROOT);
        if (info.sha256.startsWith("pending")) info.sha256 = "";
        StringBuilder log = new StringBuilder();
        JSONArray arr = j.optJSONArray("changelog");
        if (arr != null) {
            for (int i = 0; i < arr.length(); i++) {
                String line = arr.optString(i, "").trim();
                if (!line.isEmpty()) log.append("• ").append(line).append('\n');
            }
        }
        info.changelogText = log.toString().trim();
        if (TextUtils.isEmpty(info.apkUrl) || !info.apkUrl.startsWith("https://")) return null;
        return info;
    }

    private void showUpdateDialog(final UpdateInfo info) {
        if (updatePromptShown || isFinishing() || isDestroyed()) return;
        updatePromptShown = true;

        StringBuilder msg = new StringBuilder();
        msg.append("新版本 v").append(info.versionName).append("，大小约几 MB\n");
        if (!TextUtils.isEmpty(info.changelogText)) {
            msg.append('\n').append(info.changelogText);
        }
        msg.append("\n\n更新不会影响已缓存的地图与设置。");

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("发现新版本")
                .setMessage(msg.toString())
                .setPositiveButton("立即更新", null)
                .setNegativeButton("以后再说", null)
                .setCancelable(true)
                .create();
        dialog.setCanceledOnTouchOutside(false);
        dialog.show();
        // 用 show 后取按钮的方式覆盖自动 dismiss：点"立即更新"时保留 Activity 上下文，直接进下载
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(
                new android.view.View.OnClickListener() {
                    @Override
                    public void onClick(android.view.View v) {
                        dialog.dismiss();
                        startDownload(info);
                    }
                });
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(
                new android.view.View.OnClickListener() {
                    @Override
                    public void onClick(android.view.View v) {
                        rememberSkip(info.versionCode);
                        dialog.dismiss();
                    }
                });
        dialog.setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
            @Override
            public void onCancel(android.content.DialogInterface d) {
                rememberSkip(info.versionCode);
            }
        });
    }

    private void startDownload(final UpdateInfo info) {
        downloadCancelled = false;

        float d = getResources().getDisplayMetrics().density;
        int pad = (int) (22 * d);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad, pad, 0);
        downloadBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        downloadBar.setMax(100);
        downloadBar.setProgress(0);
        downloadPct = new TextView(this);
        downloadPct.setText("正在准备下载…");
        LinearLayout.LayoutParams barLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        box.addView(downloadBar, barLp);
        LinearLayout.LayoutParams txtLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        txtLp.topMargin = (int) (10 * d);
        box.addView(downloadPct, txtLp);

        downloadDialog = new AlertDialog.Builder(this)
                .setTitle("下载更新 v" + info.versionName)
                .setView(box)
                .setNegativeButton("取消", null)
                .setCancelable(false)
                .show();
        downloadDialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(
                new android.view.View.OnClickListener() {
                    @Override
                    public void onClick(android.view.View v) {
                        downloadCancelled = true;
                        downloadDialog.dismiss();
                    }
                });

        new Thread(new Runnable() {
            @Override
            public void run() {
                File result = null;
                String error = null;
                try {
                    result = downloadApk(info);
                } catch (Exception e) {
                    error = e.getMessage();
                    Log.w(TAG, "apk download failed", e);
                }
                final File apkFile = result;
                final String errMsg = error;
                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        if (downloadDialog != null && downloadDialog.isShowing()) {
                            downloadDialog.dismiss();
                        }
                        if (apkFile != null) {
                            installApkOrAskPermission(apkFile);
                        } else if (!downloadCancelled && !isFinishing() && !isDestroyed()) {
                            Toast.makeText(MainActivity.this,
                                    "更新下载失败" + (errMsg != null ? "：" + errMsg : "") + "，可稍后再试",
                                    Toast.LENGTH_LONG).show();
                        }
                    }
                });
            }
        }).start();
    }

    private File downloadApk(UpdateInfo info) throws Exception {
        File dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (dir == null) throw new IllegalStateException("存储不可用");
        if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("无法创建下载目录");

        File tmp = new File(dir, UPDATE_APK_NAME + ".tmp");
        File dest = new File(dir, UPDATE_APK_NAME);

        HttpURLConnection conn = null;
        InputStream in = null;
        OutputStream out = null;
        try {
            conn = (HttpURLConnection) new URL(info.apkUrl).openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code != 200) throw new IllegalStateException("服务器返回 " + code);
            in = conn.getInputStream();
            out = new FileOutputStream(tmp);
            byte[] buf = new byte[8192];
            long total = conn.getContentLengthLong();
            long done = 0;
            int lastPct = -1;
            int n;
            while ((n = in.read(buf)) > 0) {
                if (downloadCancelled) {
                    out.close();
                    tmp.delete();
                    return null;
                }
                out.write(buf, 0, n);
                done += n;
                if (total > 0) {
                    final int pct = (int) Math.min(99, done * 100 / total);
                    if (pct != lastPct) {
                        lastPct = pct;
                        postDownloadProgress(pct, done, total);
                    }
                }
            }
            out.flush();
            out.close();
            in.close();
            conn.disconnect();

            if (tmp.length() < 100 * 1024) {
                tmp.delete();
                throw new IllegalStateException("安装包异常（过小）");
            }
            // ZIP(APK) 魔数 PK 头快速校验
            byte[] head = new byte[2];
            FileInputStream fis = new FileInputStream(tmp);
            try {
                if (fis.read(head) != 2 || head[0] != 0x50 || head[1] != 0x4B) {
                    fis.close();
                    tmp.delete();
                    throw new IllegalStateException("安装包格式错误");
                }
            } finally {
                fis.close();
            }
            // 可选 SHA-256 校验（清单提供时强制比对）
            if (!TextUtils.isEmpty(info.sha256)) {
                String actual = sha256Hex(tmp);
                if (!actual.equals(info.sha256)) {
                    tmp.delete();
                    throw new IllegalStateException("校验失败，安装包可能已损坏");
                }
            }
            if (dest.exists()) dest.delete();
            if (!tmp.renameTo(dest)) {
                // 个别机型 rename 跨卷失败，退回拷贝
                copyFile(tmp, dest);
                tmp.delete();
            }
            postDownloadProgress(100, dest.length(), dest.length());
            return dest;
        } finally {
            try { if (out != null) out.close(); } catch (Exception ignore) { }
            try { if (in != null) in.close(); } catch (Exception ignore) { }
            if (conn != null) conn.disconnect();
        }
    }

    private void postDownloadProgress(final int pct, final long done, final long total) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (isFinishing() || isDestroyed()) return;
                if (downloadBar != null) downloadBar.setProgress(pct);
                if (downloadPct != null) {
                    if (total > 0) {
                        downloadPct.setText(String.format(Locale.ROOT,
                                "%d%%（%.1f / %.1f MB）", pct, done / 1048576.0, total / 1048576.0));
                    } else {
                        downloadPct.setText(String.format(Locale.ROOT, "已下载 %.1f MB", done / 1048576.0));
                    }
                }
            }
        });
    }

    private void installApkOrAskPermission(File apk) {
        if (!getPackageManager().canRequestPackageInstalls()) {
            pendingInstallApk = apk;
            Toast.makeText(this, "请先允许「珞珈智行」安装应用，授权后自动继续", Toast.LENGTH_LONG).show();
            try {
                Intent intent = new Intent(
                        android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + getPackageName()));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (Exception e) {
                Toast.makeText(this, "请在系统设置中允许安装未知来源应用后重试", Toast.LENGTH_LONG).show();
            }
            return;
        }
        launchInstaller(apk);
    }

    private void launchInstaller(File apk) {
        try {
            Uri uri = ApkFileProvider.uriFor(apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception e) {
            Toast.makeText(this, "无法打开系统安装器：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private static String sha256Hex(File f) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        FileInputStream fis = new FileInputStream(f);
        try {
            byte[] buf = new byte[8192];
            int n;
            while ((n = fis.read(buf)) > 0) md.update(buf, 0, n);
        } finally {
            fis.close();
        }
        StringBuilder sb = new StringBuilder(64);
        for (byte b : md.digest()) sb.append(String.format("%02x", b & 0xff));
        return sb.toString();
    }

    private static void copyFile(File src, File dst) throws Exception {
        InputStream in = new FileInputStream(src);
        OutputStream out = new FileOutputStream(dst);
        try {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } finally {
            in.close();
            out.close();
        }
    }

    private static byte[] readAllBytes(InputStream in) throws Exception {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        return bos.toByteArray();
    }

    // ============ JS 回调投递（在主线程 evaluateJavascript） ============
    private void emitVoiceEvent(final String event, final String text, final String message) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (webView == null) return;
                try {
                    JSONObject j = new JSONObject();
                    j.put("event", event);
                    if (text != null) j.put("text", text);
                    if (message != null) j.put("message", message);
                    String json = j.toString().replace("\\", "\\\\").replace("'", "\\'");
                    webView.evaluateJavascript(
                            "window.__whuWalkerVoiceCallback && window.__whuWalkerVoiceCallback('" + json + "');",
                            null);
                } catch (Exception e) {
                    Log.w(TAG, "emitVoiceEvent failed: " + e.getMessage());
                }
            }
        });
    }

    // ============ TTS 引擎状态回调（通知网页降级/提示） ============
    private void emitTtsStatus(final String status) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (webView == null) return;
                try {
                    JSONObject j = new JSONObject();
                    j.put("event", status);   // 'unavailable' | 'error'
                    String json = j.toString();
                    webView.evaluateJavascript(
                            "window.__whuWalkerTtsCallback && window.__whuWalkerTtsCallback(" + json + ");",
                            null);
                } catch (Exception e) {
                    Log.w(TAG, "emitTtsStatus failed: " + e.getMessage());
                }
            }
        });
    }

    private void attachTtsProgressListener() {
        try {
            tts.setOnUtteranceProgressListener(new android.speech.tts.UtteranceProgressListener() {
                @Override public void onStart(String utteranceId) {
                    ttsErrorCount = 0;
                }
                @Override public void onDone(String utteranceId) { }
                @Override
                public void onError(String utteranceId) {
                    onError(utteranceId, -1);
                }
                @Override
                public void onError(String utteranceId, int errorCode) {
                    ttsErrorCount++;
                    Log.w(TAG, "TTS utterance error code=" + errorCode);
                    // 连续 3 次失败：引擎大概率缺语音数据，通知网页提示一次
                    if (ttsErrorCount >= 3) {
                        ttsErrorCount = 0;
                        emitTtsStatus("error");
                    }
                }
            });
        } catch (Exception e) {
            Log.w(TAG, "attachTtsProgressListener failed: " + e.getMessage());
        }
    }

    /** 真正调用系统 TTS（必须在主线程、ttsReady 后） */
    private void speakInternal(String text) {
        if (tts == null || !ttsReady) return;
        maybeWarnMediaVolume();  // "没声音"最常见原因：媒体音量为 0（与铃声音量独立）
        int r = tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "whu-nav-" + System.nanoTime());
        if (r == TextToSpeech.ERROR) {
            ttsErrorCount++;
            if (ttsErrorCount >= 3) emitTtsStatus("error");
        }
    }

    /** TTS 走 STREAM_MUSIC；媒体音量为 0 时提示一次（铃声音量满也没声音） */
    private void maybeWarnMediaVolume() {
        if (volumeWarned) return;
        try {
            AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            if (am != null && am.getStreamVolume(AudioManager.STREAM_MUSIC) == 0) {
                volumeWarned = true;
                emitTtsStatus("volume0");
            }
        } catch (Exception e) { /* ignore */ }
    }

    // ============ ① TTS 桥 ============
    private class TtsBridge {
        /** @return true 已受理（含 init 未完成时缓存）；false 静音或引擎不可用，网页应降级 */
        @JavascriptInterface
        public boolean speak(final String text) {
            if (ttsMuted || text == null) return false;
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    if (tts == null) return;
                    if (ttsReady) {
                        speakInternal(text);
                    } else {
                        // init 未完成：TextToSpeech 此时调用会直接丢弃，缓存最新一条待播
                        pendingSpeak = text;
                    }
                }
            });
            return true;
        }

        @JavascriptInterface
        public void stop() {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    pendingSpeak = null;
                    if (tts != null) tts.stop();
                }
            });
        }

        @JavascriptInterface
        public void setMuted(boolean muted) {
            ttsMuted = muted;
            if (muted) stop();
        }

        /** 网页查询引擎是否就绪（未就绪时可选择浏览器语音兜底） */
        @JavascriptInterface
        public boolean isReady() {
            return ttsReady;
        }
    }

    // ============ ② 语音识别桥 ============
    private class VoiceBridge {
        @JavascriptInterface
        public void start(String lang) {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        // 先申请权限，授权回调里自动开始
                        pendingVoiceStart = true;
                        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, RECORD_PERM_REQ);
                        return;
                    }
                    beginVoiceInput();
                }
            });
        }

        @JavascriptInterface
        public void stop() {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    pendingVoiceStart = false;
                    destroyRecognizer();
                    // 系统语音面板无法代码关闭：仅标记忽略其返回结果
                    voiceIntentActive = false;
                    emitVoiceEvent("end", null, null);
                }
            });
        }
    }

    /**
     * 选路：优先 SpeechRecognizer（流式、体验好）；
     * 国产 ROM 无 Google 语音服务时（华为等）降级到系统 ACTION_RECOGNIZE_SPEECH 面板
     * （系统输入法/厂商语音引擎支持，全屏听写一整句）。
     */
    private void beginVoiceInput() {
        voiceFallbackUsed = false;
        boolean streamAvailable = false;
        try {
            streamAvailable = SpeechRecognizer.isRecognitionAvailable(this);
        } catch (Exception e) {
            streamAvailable = false;
        }
        if (streamAvailable) {
            startRecognition();
        } else {
            launchSystemVoicePanel();
        }
    }

    private void launchSystemVoicePanel() {
        try {
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
            intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "请说话，例如：避开陡坡，从牌坊到樱顶");
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
            voiceIntentActive = true;
            startActivityForResult(intent, ASR_INTENT_REQ);
        } catch (Exception e) {
            voiceIntentActive = false;
            // 设备上没有任何能处理语音识别的 Activity（极简 ROM）
            emitVoiceEvent("error", null, "这台设备没有可用的语音识别服务，可改用系统键盘的语音输入或打字");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != ASR_INTENT_REQ) return;
        boolean active = voiceIntentActive;
        voiceIntentActive = false;
        if (!active) return;  // 用户已点停止，结果作废
        if (resultCode == RESULT_OK && data != null) {
            ArrayList<String> list = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            String text = (list != null && !list.isEmpty()) ? list.get(0) : "";
            if (!TextUtils.isEmpty(text)) {
                emitVoiceEvent("final", text, null);
            } else {
                emitVoiceEvent("end", null, null);
            }
        } else {
            emitVoiceEvent("end", null, null);
        }
    }

    private void startRecognition() {
        destroyRecognizer();
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            launchSystemVoicePanel();
            return;
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) { }
            @Override public void onBeginningOfSpeech() { }
            @Override public void onRmsChanged(float rmsdB) { }
            @Override public void onBufferReceived(byte[] buffer) { }
            @Override public void onEndOfSpeech() { }

            @Override
            public void onError(int error) {
                // ERROR_NO_MATCH(7)/SPEECH_TIMEOUT(6)：没识别到内容，按正常结束处理，不报错打扰
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                    emitVoiceEvent("end", null, null);
                    return;
                }
                // 服务级不可用（国产 ROM 无 Google 语音服务时典型 ERROR_CLIENT=5）
                // 且本轮还没降级过 → 改走系统语音面板，避免直接把错误甩给用户
                boolean serviceDead = error == SpeechRecognizer.ERROR_CLIENT
                        || error == SpeechRecognizer.ERROR_SERVER
                        || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                        || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE
                        || error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED;
                if (serviceDead && !voiceFallbackUsed) {
                    voiceFallbackUsed = true;
                    destroyRecognizer();
                    Log.i(TAG, "SpeechRecognizer dead (" + error + "), fallback to system voice panel");
                    launchSystemVoicePanel();
                    return;
                }
                emitVoiceEvent("error", null, asrErrorMessage(error));
            }

            @Override
            public void onResults(Bundle results) {
                ArrayList<String> list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String text = (list != null && !list.isEmpty()) ? list.get(0) : "";
                emitVoiceEvent("final", text, null);
                destroyRecognizer();
            }

            @Override
            public void onPartialResults(Bundle partialResults) {
                ArrayList<String> list = partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list != null && !list.isEmpty() && list.get(0) != null && !list.get(0).isEmpty()) {
                    emitVoiceEvent("partial", list.get(0), null);
                }
            }

            @Override public void onEvent(int eventType, Bundle params) { }
        });

        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        recognizer.startListening(intent);
    }

    private String asrErrorMessage(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_AUDIO: return "录音设备异常";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS: return "麦克风权限被拒绝";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY: return "语音服务忙，请稍候再试";
            case SpeechRecognizer.ERROR_CLIENT: return "本机语音服务不可用，请尝试输入法语音输入";
            case SpeechRecognizer.ERROR_SERVER: return "语音服务未响应";
            case SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED:
            case SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE: return "语音语言包不可用";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "语音服务网络异常";
            default: return "语音识别失败，请重试";
        }
    }

    private void destroyRecognizer() {
        if (recognizer != null) {
            try {
                recognizer.cancel();
                recognizer.destroy();
            } catch (Exception e) { /* ignore */ }
            recognizer = null;
        }
    }

    // ============ ④ 原生定位 + 罗盘融合 ============
    // WebView 内 navigator.geolocation 在部分国产 ROM 上存在授权时序问题
    // （页面加载即 watch，授权弹窗尚未返回，句柄拿到 PERMISSION_DENIED 后死亡，
    //   授权返回也不会恢复）。改用原生桥后，权限授予 -> 立即启动定位，时序确定。
    // 航向融合：步行速度 >=0.6m/s 且 GPS bearing 新鲜（3s 内）用 GPS 航向；
    // 否则用 ROTATION_VECTOR（或加速度+磁力计）算出的磁罗盘航向 + 磁偏角修正。
    private LocationManager locationManager;
    private SensorManager sensorManager;
    private LocationListener gpsLocationListener;
    private LocationListener netLocationListener;
    private SensorEventListener rotationListener;
    private SensorEventListener accMagListener;
    private final float[] lastAccel = new float[3];
    private final float[] lastMag = new float[3];
    private boolean hasAccel = false, hasMag = false;
    private volatile Location lastFix;
    private float gpsBearing = Float.NaN;
    private long gpsBearingTs = 0L;
    private float compassBearing = Float.NaN;
    private float fusedHeading = Float.NaN;
    private float declinationDeg = 0f;
    private boolean locRunning = false;
    private boolean pendingLocationStart = false;
    private final Handler locHandler = new Handler(Looper.getMainLooper());
    private static final long LOC_EMIT_INTERVAL_MS = 500L;
    private static final float GPS_BEARING_MIN_SPEED = 0.6f;   // m/s
    private static final long GPS_BEARING_FRESH_MS = 3000L;

    private final Runnable locEmitTask = new Runnable() {
        @Override
        public void run() {
            emitLocation();
            if (locRunning) locHandler.postDelayed(this, LOC_EMIT_INTERVAL_MS);
        }
    };

    private class LocationBridge {
        @JavascriptInterface
        public void start() {
            mainHandler.post(new Runnable() {
                @Override public void run() { startNativeLocation(); }
            });
        }

        @JavascriptInterface
        public void stop() {
            mainHandler.post(new Runnable() {
                @Override public void run() { stopNativeLocation(); }
            });
        }
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void startNativeLocation() {
        if (locRunning) return;
        if (!hasLocationPermission()) {
            // 权限弹窗尚未返回；授权成功回调里自动真正启动（解决 WebView 时序死亡）
            pendingLocationStart = true;
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
            }, LOC_PERM_REQ);
            return;
        }
        pendingLocationStart = false;
        ensureLocObjects();

        boolean anyProvider = false;
        try {
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER,
                        1000L, 0f, gpsLocationListener, Looper.getMainLooper());
                Location last = locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER);
                if (last != null) lastFix = last;
                anyProvider = true;
            }
        } catch (SecurityException se) {
            Log.w(TAG, "gps provider register failed: " + se.getMessage());
        }
        try {
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.NETWORK_PROVIDER,
                        2000L, 0f, netLocationListener, Looper.getMainLooper());
                Location last = locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER);
                if (last != null && (lastFix == null || last.getTime() > lastFix.getTime())) lastFix = last;
                anyProvider = true;
            }
        } catch (SecurityException se) {
            Log.w(TAG, "network provider register failed: " + se.getMessage());
        }
        if (!anyProvider) {
            emitLocStatus("error", "系统定位服务未开启，请打开位置信息（GPS），或在地图上手动选点");
        }

        if (sensorManager == null) {
            sensorManager = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        }
        if (sensorManager != null) {
            Sensor rot = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR);
            if (rot != null) {
                sensorManager.registerListener(rotationListener, rot, SensorManager.SENSOR_DELAY_UI, locHandler);
            } else {
                // 少数设备无旋转矢量：加速度 + 磁力计组合算方位
                Sensor acc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
                Sensor mag = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD);
                if (acc != null) sensorManager.registerListener(accMagListener, acc, SensorManager.SENSOR_DELAY_UI, locHandler);
                if (mag != null) sensorManager.registerListener(accMagListener, mag, SensorManager.SENSOR_DELAY_UI, locHandler);
            }
        }

        locRunning = true;
        fusedHeading = Float.NaN;
        locHandler.removeCallbacks(locEmitTask);
        locHandler.post(locEmitTask);
    }

    private void stopNativeLocation() {
        locRunning = false;
        pendingLocationStart = false;
        locHandler.removeCallbacks(locEmitTask);
        if (locationManager != null) {
            try {
                if (gpsLocationListener != null) locationManager.removeUpdates(gpsLocationListener);
                if (netLocationListener != null) locationManager.removeUpdates(netLocationListener);
            } catch (Exception e) { /* ignore */ }
        }
        if (sensorManager != null) {
            if (rotationListener != null) sensorManager.unregisterListener(rotationListener);
            if (accMagListener != null) sensorManager.unregisterListener(accMagListener);
        }
        gpsBearing = Float.NaN;
        fusedHeading = Float.NaN;
    }

    private void ensureLocObjects() {
        if (locationManager == null) {
            locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        }
        if (gpsLocationListener == null) {
            gpsLocationListener = new NativeLocationListener();
            netLocationListener = new NativeLocationListener();
        }
        if (rotationListener == null) {
            rotationListener = new SensorEventListener() {
                @Override
                public void onSensorChanged(SensorEvent event) {
                    float[] r = new float[9];
                    SensorManager.getRotationMatrixFromVector(r, event.values);
                    updateCompass(r);
                }
                @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }
            };
        }
        if (accMagListener == null) {
            accMagListener = new SensorEventListener() {
                @Override
                public void onSensorChanged(SensorEvent event) {
                    if (event.sensor.getType() == Sensor.TYPE_ACCELEROMETER) {
                        System.arraycopy(event.values, 0, lastAccel, 0, 3);
                        hasAccel = true;
                    } else if (event.sensor.getType() == Sensor.TYPE_MAGNETIC_FIELD) {
                        System.arraycopy(event.values, 0, lastMag, 0, 3);
                        hasMag = true;
                    }
                    if (hasAccel && hasMag) {
                        float[] r = new float[9];
                        if (SensorManager.getRotationMatrix(r, null, lastAccel, lastMag)) updateCompass(r);
                    }
                }
                @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }
            };
        }
    }

    private class NativeLocationListener implements LocationListener {
        @Override
        public void onLocationChanged(Location loc) {
            lastFix = loc;
            // 磁偏角随位置变化（武汉约 -3.7°）
            try {
                GeomagneticField gf = new GeomagneticField(
                        (float) loc.getLatitude(), (float) loc.getLongitude(),
                        loc.hasAltitude() ? (float) loc.getAltitude() : 0f,
                        loc.getTime());
                declinationDeg = gf.getDeclination();
            } catch (Exception e) { /* 保留旧磁偏角 */ }
            // 静止时 GPS bearing 会漂/归零，必须用速度门控
            if (loc.hasBearing() && loc.hasSpeed() && loc.getSpeed() >= GPS_BEARING_MIN_SPEED) {
                gpsBearing = loc.getBearing();
                gpsBearingTs = loc.getTime();
            }
            emitLocation();
        }
        @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
        @Override public void onProviderEnabled(String provider) { }
        @Override public void onProviderDisabled(String provider) { }
    }

    /** 由旋转矩阵求设备朝向（屏幕顶部指向的方位角，0=北，顺时针），含屏幕旋转补偿与磁偏角修正 */
    private void updateCompass(float[] r) {
        int axisX = SensorManager.AXIS_X;
        int axisY = SensorManager.AXIS_Y;
        int rotation = ((WindowManager) getSystemService(Context.WINDOW_SERVICE))
                .getDefaultDisplay().getRotation();
        switch (rotation) {
            case Surface.ROTATION_90:  axisX = SensorManager.AXIS_Y;        axisY = SensorManager.AXIS_MINUS_X; break;
            case Surface.ROTATION_180: axisX = SensorManager.AXIS_MINUS_X;  axisY = SensorManager.AXIS_MINUS_Y; break;
            case Surface.ROTATION_270: axisX = SensorManager.AXIS_MINUS_Y;  axisY = SensorManager.AXIS_X;       break;
            default: break;
        }
        float[] adjusted = new float[9];
        SensorManager.remapCoordinateSystem(r, axisX, axisY, adjusted);
        float[] orient = new float[3];
        SensorManager.getOrientation(adjusted, orient);
        double trueDeg = (Math.toDegrees(orient[0]) + declinationDeg + 360.0) % 360.0;
        // 罗盘噪声大：重低通；NaN 时直接初始化
        if (Float.isNaN(compassBearing)) {
            compassBearing = (float) trueDeg;
        } else {
            float diff = (float) wrapAngle(trueDeg - compassBearing, -180.0, 180.0);
            compassBearing = (float) wrapAngle(compassBearing + diff * 0.25f, 0.0, 360.0);
        }
    }

    private static double wrapAngle(double a, double min, double max) {
        double range = max - min;
        a = (a - min) % range;
        if (a < 0) a += range;
        return a + min;
    }

    private void emitLocation() {
        Location fix = lastFix;
        if (fix == null || webView == null) return;
        long now = System.currentTimeMillis();
        float heading = Float.NaN;
        // 步行运动时用 GPS 航向（转向响应快），静止/低速用罗盘（原地转身也能动）
        if (!Float.isNaN(gpsBearing) && now - gpsBearingTs <= GPS_BEARING_FRESH_MS
                && fix.hasSpeed() && fix.getSpeed() >= GPS_BEARING_MIN_SPEED) {
            heading = gpsBearing;
        } else if (!Float.isNaN(compassBearing)) {
            heading = compassBearing;
        }
        if (!Float.isNaN(heading)) {
            if (Float.isNaN(fusedHeading)) {
                fusedHeading = heading;
            } else {
                float d = (float) wrapAngle(heading - fusedHeading, -180.0, 180.0);
                fusedHeading = (float) wrapAngle(fusedHeading + d * 0.4f, 0.0, 360.0);
            }
        }
        final double lat = fix.getLatitude();
        final double lng = fix.getLongitude();
        final float acc = fix.hasAccuracy() ? fix.getAccuracy() : -1f;
        final float speed = fix.hasSpeed() ? fix.getSpeed() : 0f;
        final String src = fix.getProvider() != null ? fix.getProvider() : "gps";
        final float outHeading = fusedHeading;
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (webView == null) return;
                try {
                    JSONObject j = new JSONObject();
                    j.put("event", "fix");
                    j.put("lat", lat);
                    j.put("lng", lng);
                    j.put("accuracy", acc);
                    j.put("speed", speed);
                    j.put("src", src);
                    j.put("ts", System.currentTimeMillis());
                    if (Float.isNaN(outHeading)) j.put("heading", JSONObject.NULL);
                    else j.put("heading", Math.round(outHeading * 10) / 10.0);
                    String json = j.toString();
                    webView.evaluateJavascript(
                            "window.__whuWalkerLocationCallback && window.__whuWalkerLocationCallback(" + json + ");",
                            null);
                } catch (Exception e) {
                    Log.w(TAG, "emitLocation failed: " + e.getMessage());
                }
            }
        });
    }

    private void emitLocStatus(final String event, final String message) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (webView == null) return;
                try {
                    JSONObject j = new JSONObject();
                    j.put("event", event);
                    j.put("message", message);
                    String json = j.toString();
                    webView.evaluateJavascript(
                            "window.__whuWalkerLocationCallback && window.__whuWalkerLocationCallback(" + json + ");",
                            null);
                } catch (Exception e) { /* ignore */ }
            }
        });
    }

    // ============ ③ 屏幕常亮桥（导航中开启，退出导航恢复） ============
    private class ScreenBridge {
        @JavascriptInterface
        public void setKeepScreenOn(final boolean on) {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    if (on) {
                        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    } else {
                        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    }
                }
            });
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // 从"允许安装未知来源"设置页返回：已授权则自动拉起安装器
        if (pendingInstallApk != null) {
            final File apk = pendingInstallApk;
            if (getPackageManager().canRequestPackageInstalls()) {
                pendingInstallApk = null;
                if (apk.exists()) launchInstaller(apk);
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == RECORD_PERM_REQ) {
            boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted && pendingVoiceStart) {
                pendingVoiceStart = false;
                beginVoiceInput();
            } else if (!granted) {
                pendingVoiceStart = false;
                emitVoiceEvent("error", null, "麦克风权限被拒绝，可在系统设置中开启");
            }
        } else if (requestCode == LOC_PERM_REQ) {
            boolean granted = false;
            for (int g : grantResults) {
                if (g == PackageManager.PERMISSION_GRANTED) { granted = true; break; }
            }
            // 关键修复：授权一返回立即启动原生定位（旧 WebView 链路此时 watch 句柄已死）
            if (granted) {
                pendingLocationStart = false;
                if (!locRunning) startNativeLocation();
            } else if (pendingLocationStart) {
                pendingLocationStart = false;
                emitLocStatus("error", "定位权限被拒绝，可在系统设置中开启，或直接在地图上手动选点");
            }
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null) webView.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        destroyRecognizer();
        stopNativeLocation();
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
