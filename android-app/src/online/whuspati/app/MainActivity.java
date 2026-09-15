package online.whuspati.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
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
 *
 * 应用内更新（原生，无 JS 桥）：启动数秒后拉取 /app/latest.json，
 * versionCode 更高时弹窗 → 后台下载 APK（SHA-256 校验）→ 授权安装未知来源
 * → 经 ApkFileProvider 拉起系统安装器，用户点一次确认即可覆盖安装。
 */
public class MainActivity extends Activity {

    private static final int LOC_PERM_REQ = 1;
    private static final int RECORD_PERM_REQ = 2;
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
    private SpeechRecognizer recognizer;
    private boolean pendingVoiceStart = false;  // 等麦克风授权后自动开识别

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

        // 原生 TTS：init 回调里设普通话；任何状态下 speak 都入队，init 未完成时系统会等待
        tts = new TextToSpeech(getApplicationContext(), new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                if (status == TextToSpeech.SUCCESS) {
                    int r = tts.setLanguage(Locale.SIMPLIFIED_CHINESE);
                    if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
                        tts.setLanguage(Locale.getDefault());
                    }
                    tts.setSpeechRate(1.0f);
                    tts.setPitch(1.0f);
                } else {
                    Log.w(TAG, "TTS init failed: " + status);
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
        s.setUserAgentString(s.getUserAgentString() + " WHUWalkerApp/1.2");

        webView.addJavascriptInterface(new TtsBridge(), "WhuWalkerTts");
        webView.addJavascriptInterface(new VoiceBridge(), "WhuWalkerVoice");
        webView.addJavascriptInterface(new ScreenBridge(), "WhuWalkerScreen");

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

    // ============ ① TTS 桥 ============
    private class TtsBridge {
        @JavascriptInterface
        public void speak(final String text) {
            if (ttsMuted || text == null) return;
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    if (tts == null) return;
                    // QUEUE_FLUSH：新导航指令打断旧播报
                    tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "whu-nav-" + System.nanoTime());
                }
            });
        }

        @JavascriptInterface
        public void stop() {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    if (tts != null) tts.stop();
                }
            });
        }

        @JavascriptInterface
        public void setMuted(boolean muted) {
            ttsMuted = muted;
            if (muted) stop();
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
                    startRecognition();
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
                    emitVoiceEvent("end", null, null);
                }
            });
        }
    }

    private void startRecognition() {
        destroyRecognizer();
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            emitVoiceEvent("error", null, "这台设备没有可用的语音识别服务");
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
                // ERROR_NO_MATCH(7)：没识别到内容，按正常结束处理，不报错打扰
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                    emitVoiceEvent("end", null, null);
                } else {
                    emitVoiceEvent("error", null, asrErrorMessage(error));
                }
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
                startRecognition();
            } else if (!granted) {
                pendingVoiceStart = false;
                emitVoiceEvent("error", null, "麦克风权限被拒绝，可在系统设置中开启");
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
