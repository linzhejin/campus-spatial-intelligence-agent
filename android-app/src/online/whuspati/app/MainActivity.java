package online.whuspati.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;
import android.view.WindowManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.speech.tts.TextToSpeech;

import org.json.JSONObject;

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
 */
public class MainActivity extends Activity {

    private static final int LOC_PERM_REQ = 1;
    private static final int RECORD_PERM_REQ = 2;
    private static final String TAG = "WhuWalker";

    private WebView webView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private TextToSpeech tts;
    private boolean ttsMuted = false;
    private SpeechRecognizer recognizer;
    private boolean pendingVoiceStart = false;  // 等麦克风授权后自动开识别

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
        s.setUserAgentString(s.getUserAgentString() + " WHUWalkerApp/1.1");

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
