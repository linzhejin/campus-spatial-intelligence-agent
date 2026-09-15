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
    var nativeBroken = false;   // 原生 TTS 引擎确认不可用/连续失败后，不再走桥
    var warnedOnce = {};        // 各类提示每会话最多弹一次

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

    // 自绘轻提示（不依赖 app.js，避免循环耦合）：3.5s 自动消失，可点关
    function showVoiceHint(msg) {
        try {
            var el = document.createElement('div');
            el.textContent = msg;
            el.style.cssText = 'position:fixed;left:50%;bottom:118px;transform:translateX(-50%);z-index:1400;'
                + 'max-width:88%;padding:10px 14px;background:rgba(33,33,33,0.92);color:#fff;'
                + 'font-size:13px;line-height:1.5;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.3);'
                + 'pointer-events:auto;cursor:pointer;text-align:center;';
            document.body.appendChild(el);
            var timer = setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 3500);
            el.addEventListener('click', function () {
                clearTimeout(timer);
                if (el.parentNode) el.parentNode.removeChild(el);
            });
        } catch (e) { /* ignore */ }
    }

    function notifyTtsProblem(kind) {
        if (warnedOnce[kind]) return;
        warnedOnce[kind] = true;
        if (kind === 'volume0') {
            showVoiceHint('手机媒体音量为 0，调高音量即可听到导航播报');
        } else {
            nativeBroken = true;
            showVoiceHint('系统语音引擎不可用，本次导航请看屏幕上的转向提示；'
                + '可在系统「设置 → 语言和输入法 → 文字转语音」中启用语音引擎');
        }
    }

    // 原生引擎状态回调（MainActivity.emitTtsStatus 注入）
    // event: 'unavailable' 无中文语音数据/init 失败；'error' 连续播报失败；'volume0' 媒体音量为零
    window.__whuWalkerTtsCallback = function (d) {
        if (!d || !d.event) return;
        console.warn('[TTS] 原生引擎状态:', d.event);
        if (d.event === 'volume0') {
            notifyTtsProblem('volume0');
        } else {
            notifyTtsProblem('engine');
        }
    };

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
            var msg = String(text);
            var bridge = nativeBroken ? null : apkBridge();
            if (bridge) {
                try {
                    // 桥返回 false = 未受理（静音/引擎不可用）→ 尝试浏览器 synth 兜底
                    var accepted = bridge.speak(msg);
                    if (accepted === false) speakViaBrowser(msg);
                    return;
                } catch (e) { /* 桥异常降级浏览器 */ }
            }
            speakViaBrowser(msg);
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
