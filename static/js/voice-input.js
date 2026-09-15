/* ========================================================================
   珞珈智行 · 语音输入（voice-input.js）
   · 浏览器/PWA：Web Speech API（webkitSpeechRecognition，zh-CN，实时转写）
   · APK 内：Android 原生 SpeechRecognizer（window.WhuWalkerVoice 桥）
       原生端通过 window.__whuWalkerVoiceCallback(json) 回传：
       {event:'partial'|'final'|'error'|'end', text?:string, message?:string}
   · 微信内置浏览器/不支持环境：回调 onUnsupported，由 app.js 弹"在浏览器打开"引导
   · Fail-soft：任何异常只走回调，不抛错
   ======================================================================== */
(function () {
    'use strict';

    var SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    var recognition = null;
    var listening = false;
    var manualStop = false;
    var hooks = null;

    function isWeChat() {
        return /MicroMessenger/i.test(navigator.userAgent || '');
    }

    // Android 原生语音桥
    function apkBridge() {
        try {
            if (window.WhuWalkerVoice && typeof window.WhuWalkerVoice.start === 'function') {
                return window.WhuWalkerVoice;
            }
        } catch (e) { /* ignore */ }
        return null;
    }

    // 原生端统一回调入口（APK 通过 evaluateJavascript 调用）
    window.__whuWalkerVoiceCallback = function (payload) {
        var ev;
        try {
            ev = typeof payload === 'string' ? JSON.parse(payload) : payload;
        } catch (e) {
            console.warn('[ASR] 原生回调解析失败:', payload);
            return;
        }
        if (!ev || !hooks) return;
        if (ev.event === 'partial' && ev.text) {
            if (hooks.onInterim) hooks.onInterim(ev.text);
        } else if (ev.event === 'final') {
            var text = (ev.text || '').trim();
            listening = false;
            if (hooks.onStateChange) hooks.onStateChange('idle');
            if (text && hooks.onFinal) hooks.onFinal(text);
            hooks = null;
        } else if (ev.event === 'error') {
            listening = false;
            if (hooks.onStateChange) hooks.onStateChange('idle');
            if (hooks.onError) hooks.onError(ev.message || '语音识别失败');
            hooks = null;
        } else if (ev.event === 'end') {
            if (listening) {
                listening = false;
                if (hooks.onStateChange) hooks.onStateChange('idle');
            }
        }
    };

    function startViaApk(cb) {
        var bridge = apkBridge();
        try {
            bridge.start('zh-CN');
            listening = true;
            if (cb.onStateChange) cb.onStateChange('listening');
        } catch (e) {
            if (cb.onUnsupported) cb.onUnsupported('unsupported');
        }
    }

    function startViaBrowser(cb) {
        if (!SR) {
            cb.onUnsupported && cb.onUnsupported(isWeChat() ? 'wechat' : 'unsupported');
            return;
        }
        try {
            recognition = new SR();
        } catch (e) {
            cb.onUnsupported && cb.onUnsupported('unsupported');
            return;
        }
        recognition.lang = 'zh-CN';
        recognition.interimResults = true;
        recognition.continuous = false;   // 说一句话就出结果（导航场景的短查询）
        recognition.maxAlternatives = 1;

        manualStop = false;

        recognition.onstart = function () {
            listening = true;
            if (cb.onStateChange) cb.onStateChange('listening');
        };

        recognition.onresult = function (e) {
            var interim = '';
            var finalText = '';
            for (var i = e.resultIndex; i < e.results.length; i++) {
                var r = e.results[i];
                if (r.isFinal) finalText += r[0].transcript;
                else interim += r[0].transcript;
            }
            if (interim && cb.onInterim) cb.onInterim(interim);
            if (finalText && cb.onFinal) cb.onFinal(finalText.trim());
        };

        recognition.onerror = function (e) {
            listening = false;
            if (cb.onStateChange) cb.onStateChange('idle');
            // not-allowed / service-not-allowed → 没麦克风权限；no-speech → 没说话
            var map = {
                'not-allowed': '麦克风权限被拒绝，可在浏览器设置中开启',
                'service-not-allowed': '麦克风不可用',
                'no-speech': '没有听到声音，再试一次吧',
                'network': '语音服务网络异常',
                'audio-capture': '找不到麦克风设备',
            };
            if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
                cb.onUnsupported && cb.onUnsupported('permission');
            } else if (cb.onError) {
                cb.onError(map[e.error] || '语音识别失败，请打字或重试');
            }
        };

        recognition.onend = function () {
            if (listening) {
                listening = false;
                if (cb.onStateChange) cb.onStateChange('idle');
            }
        };

        try {
            recognition.start();
        } catch (e) {
            // 上一次 start 还没结束就再点 → 忽略
            cb.onError && cb.onError('语音启动中，请稍候');
        }
    }

    var VoiceInput = {
        /**
         * @param {Object} cb
         *   onInterim(text) 实时转写（可直接写进输入框）
         *   onFinal(text)   说完了，拿到整句（调用方自动提交查询）
         *   onStateChange(s) 'listening' | 'idle'
         *   onUnsupported(reason) 'wechat' | 'unsupported' | 'permission'
         *   onError(msg)
         */
        start: function (cb) {
            if (listening) return;
            hooks = cb || {};
            if (apkBridge()) {
                startViaApk(hooks);
            } else {
                startViaBrowser(hooks);
            }
        },

        stop: function () {
            manualStop = true;
            if (recognition) {
                try { recognition.stop(); } catch (e) { /* ignore */ }
            }
            var bridge = apkBridge();
            if (bridge && typeof bridge.stop === 'function') {
                try { bridge.stop(); } catch (e) { /* ignore */ }
            }
            listening = false;
            hooks = null;
        },

        isListening: function () { return listening; },

        /** {ok:boolean, reason?:'wechat'|'unsupported'} */
        capability: function () {
            if (apkBridge() || SR) return { ok: true };
            return { ok: false, reason: isWeChat() ? 'wechat' : 'unsupported' };
        },
    };

    window.WhuWalkerVoiceInput = VoiceInput;
})();
