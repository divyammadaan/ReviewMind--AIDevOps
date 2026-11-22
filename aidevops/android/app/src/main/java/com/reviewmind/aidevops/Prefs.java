package com.reviewmind.aidevops;

import android.content.Context;
import android.content.SharedPreferences;

/** The few things the app remembers: where the control plane is, the access token, and alert settings. */
final class Prefs {
    private Prefs() {}

    private static SharedPreferences sp(Context c) {
        return c.getApplicationContext().getSharedPreferences("reviewmind", Context.MODE_PRIVATE);
    }

    static String base(Context c) { return sp(c).getString("base", ""); }
    static String token(Context c) { return sp(c).getString("token", ""); }
    static boolean configured(Context c) { return !base(c).isEmpty() && !token(c).isEmpty(); }

    static void save(Context c, String base, String token) {
        sp(c).edit().putString("base", base).putString("token", token).putLong("seen", 0).apply();
    }

    static void clear(Context c) { sp(c).edit().clear().apply(); }

    /** Seconds between checks while the app watches in the background. */
    static int interval(Context c) { return sp(c).getInt("interval", 5); }
    static void setInterval(Context c, int s) { sp(c).edit().putInt("interval", s).apply(); }

    static boolean alerts(Context c) { return sp(c).getBoolean("alerts", true); }
    static void setAlerts(Context c, boolean on) { sp(c).edit().putBoolean("alerts", on).apply(); }

    /** Time (ms) of the newest alert already shown, so the same alert is never shown twice. 0 = nothing seen yet. */
    static long seen(Context c) { return sp(c).getLong("seen", 0); }
    static void setSeen(Context c, long ms) { sp(c).edit().putLong("seen", ms).apply(); }

    /** Accepts "192.168.1.5:9000", "http://host:9000/" and so on; returns "http://host:9000". */
    static String normalize(String raw) {
        String s = raw.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (s.isEmpty()) return s;
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        return s;
    }
}
