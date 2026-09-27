(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.WHURouteState = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    function clone(value) {
        return value == null ? value : JSON.parse(JSON.stringify(value));
    }

    function createStore(initial) {
        var committed = clone(initial || null);
        var seq = 0;
        var active = 0;
        return {
            current: function () { return clone(committed); },
            replace: function (value) { committed = clone(value); },
            begin: function (change) {
                active = ++seq;
                return { id: active, change: clone(change) };
            },
            commit: function (token, value) {
                if (!token || token.id !== active) return false;
                committed = clone(value);
                return true;
            },
            rollback: function (token) {
                if (!token || token.id !== active) return false;
                active = 0;
                return true;
            },
        };
    }

    return { createStore: createStore };
});
