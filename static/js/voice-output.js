/* ========================================================================
   珞珈智行 · 语音播报（voice-output.js）
   · APK 内优先走 Android 原生 TextToSpeech（window.WhuWalkerTts 桥）
   · 浏览器/PWA 降级 Web Speech API（speechSynthesis，zh-CN）
   · 语义：speak() 一律 QUEUE_FLUSH（新指令打断旧播报，导航场景必须及时）
   · 静音偏好持久化 localStorage；任何异常 fail-soft，绝不影响导航主流程
   ======================================================================== */
(function () {
    'use strict';

    var MUTE_KEY = 'whu_walker:voice_muted';
    var synth = window.speechSynthesis || null;
    var zhVoice = null;
    var voicesTried = false;

    function safeLsGet(k) {
        try { return window.localStorage.getItem(k); } catch (e) { return null; }
    }
    function safeLsSet(k, v) {
        try { window.localStorage.setItem(k, v); } catch (e) { /* ignore */ }
    }

    var muted = safeLsGet(MUTE_KEY) === '1';

    // 提前挑一个中文语音（Chrome 语音表异步加载，需要 onvoiceschanged）
    function pickZhVoice() {
        if (!synth || voicesTried) return;
        try {
            var list = synth.getVoices() || [];
            if (!list.length) return;
            voicesTried = true;
            // 优先大陆普通话，其次任意中文
            zhVoice = list.filter(function (v) { return /zh(-|_)?(CN|Hans)/i.test(v.lang); })[0]
                || list.filter(function (v) { return /^zh/i.test(v.lang); })[0]
                || null;
        } catch (e) { /* ignore */ }
    }
    if (synth) {
        pickZhVoice();
        synth.onvoiceschanged = pickZhVoice;
        // iOS Safari：长会话中 synth 会卡死，周期性 resume
        setInterval(function () {
            try { if (synth.paused) synth.resume(); } catch (e) { /* ignore */ }
        }, 5000);
    }

    // Android 原生桥是否就绪（APK MainActivity 注入 WhuWalkerTts）
    function apkBridge() {
        try {
            if (window.WhuWalkerTts && typeof window.WhuWalkerTts.speak === 'function') {
                return window.WhuWalkerTts;
            }
        } catch (e) { /* 跨域访问异常时视为不存在 */ }
        return null;
    }

    function speakViaBrowser(text) {
        if (!synth) return false;
        try {
            synth.cancel();  // QUEUE_FLUSH：清掉排队中的旧指令
            var u = new SpeechSynthesisUtterance(text);
            u.lang = 'zh-CN';
            u.rate = 1.0;
            u.pitch = 1.0;
            u.volume = 1.0;
            if (zhVoice) u.voice = zhVoice;
            synth.speak(u);
            return true;
        } catch (e) {
            console.warn('[TTS] speechSynthesis 播报失败:', e);
            return false;
        }
    }

    var VoiceOutput = {
        /**
         * 播报一条指令（打断当前播报）。
         * @param {string} text
         * @param {{priority?: boolean}} [opts] priority 目前与普通同级（均 flush），预留
         */
        speak: function (text, opts) {
            if (!text) return;
            if (muted) return;
            var bridge = apkBridge();
            if (bridge) {
                try { bridge.speak(String(text)); return; } catch (e) { /* 桥异常降级浏览器 */ }
            }
            speakViaBrowser(String(text));
        },

        stop: function () {
            var bridge = apkBridge();
            if (bridge) {
                try { bridge.stop(); } catch (e) { /* ignore */ }
            }
            if (synth) {
                try { synth.cancel(); } catch (e) { /* ignore */ }
            }
        },

        isMuted: function () { return muted; },

        setMuted: function (v) {
            muted = !!v;
            safeLsSet(MUTE_KEY, muted ? '1' : '0');
            if (muted) this.stop();
            // 通知 APK 端同步静音状态（若桥支持）
            var bridge = apkBridge();
            if (bridge && typeof bridge.setMuted === 'function') {
                try { bridge.setMuted(muted); } catch (e) { /* ignore */ }
            }
        },

        toggleMuted: function () {
            this.setMuted(!muted);
            return muted;
        },

        /** 播报前预热（用户点「开始导航」时调用，解锁移动端语音自动播放限制） */
        warmup: function () {
            if (muted) return;
            var bridge = apkBridge();
            if (bridge) return;
            if (synth) {
                try {
                    var u = new SpeechSynthesisUtterance(' ');
                    u.volume = 0;
                    synth.speak(u);
                } catch (e) { /* ignore */ }
            }
        },

        supported: function () { return !!(apkBridge() || synth); },
    };

    // 页面隐藏/关闭时停止播报，避免后台念念有词
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') VoiceOutput.stop();
    });
    window.addEventListener('pagehide', function () { VoiceOutput.stop(); });

    window.WhuWalkerVoiceOutput = VoiceOutput;
})();
