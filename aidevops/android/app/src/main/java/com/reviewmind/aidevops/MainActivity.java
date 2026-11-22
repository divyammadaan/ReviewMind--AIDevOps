package com.reviewmind.aidevops;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.BackgroundColorSpan;
import android.text.style.ForegroundColorSpan;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The whole app in one screen-switching activity: connect, services, incident (review and approve a fix),
 * alerts history and settings. Plain Android views, no libraries.
 */
public class MainActivity extends Activity {
    // palette
    private static final int BG = 0xFFF4F6F9, CARD = 0xFFFFFFFF, INK = 0xFF1B2430, MUTED = 0xFF6B7685, LINE = 0xFFE1E6ED;
    private static final int ACCENT = 0xFF2F5BEA, GREEN = 0xFF1E9E5A, RED = 0xFFD6403A, AMBER = 0xFFC77D00, NAVY = 0xFF1B2A4A;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private FrameLayout root;
    private LinearLayout content;       // the part that changes
    private TextView banner;            // "cannot reach the server" bar
    private TextView subtitle;
    private LinearLayout tabs;

    private String screen = "services"; // services | alerts | settings | incident
    private String incident = null;
    private JSONObject status;          // last good /m/api/status
    private JSONArray alertList;        // last good notifications
    private String statusKey = "", alertKey = "";
    private boolean armed = false;      // approve needs two taps
    private final Runnable disarm = () -> { armed = false; render(); };
    private boolean visible = false;
    private boolean busy = false;

    // ------------------------------------------------------------------------------------------ lifecycle
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(NAVY);
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        start(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        start(intent);
    }

    private void start(Intent intent) {
        if (!Prefs.configured(this)) {
            showConnect(null);
            return;
        }
        buildShell();
        String svc = intent == null ? null : intent.getStringExtra("svc");
        if (svc != null && !svc.isEmpty()) {
            screen = "incident";
            incident = svc;
        }
        watchInBackground();
        render();
        poll();
    }

    @Override
    protected void onResume() {
        super.onResume();
        visible = true;
        ui.post(tick);
    }

    @Override
    protected void onPause() {
        visible = false;
        ui.removeCallbacks(tick);
        super.onPause();
    }

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (!visible) return;
            if (Prefs.configured(MainActivity.this)) poll();
            ui.postDelayed(this, 4000);
        }
    };

    @Override
    public void onBackPressed() {
        if (Prefs.configured(this) && screen.equals("incident")) {
            screen = "services";
            incident = null;
            armed = false;
            render();
        } else if (Prefs.configured(this) && !screen.equals("services")) {
            screen = "services";
            render();
        } else {
            super.onBackPressed();
        }
    }

    private void watchInBackground() {
        if (Prefs.alerts(this)) {
            try {
                startForegroundService(new Intent(this, MonitorService.class));
            } catch (Exception ignored) {
                // the system refused; the app still updates while it is open
            }
        } else {
            stopService(new Intent(this, MonitorService.class));
        }
    }

    // ------------------------------------------------------------------------------------------- networking
    private void poll() {
        if (busy) return;
        busy = true;
        final String base = Prefs.base(this), token = Prefs.token(this);
        final boolean wantAlerts = screen.equals("alerts");
        io.execute(() -> {
            Api.Result s = Api.get(base, token, "/m/api/status");
            Api.Result a = wantAlerts && s.ok() ? Api.get(base, token, "/m/api/notifications") : null;
            ui.post(() -> {
                busy = false;
                onData(s, a);
            });
        });
    }

    private void onData(Api.Result s, Api.Result a) {
        if (banner == null) return;
        if (!s.ok()) {
            banner.setText(s.problem());
            banner.setVisibility(View.VISIBLE);
            return;
        }
        banner.setVisibility(View.GONE);
        boolean changed = false;
        if (!s.body.equals(statusKey)) {
            statusKey = s.body;
            status = s.json();
            changed = true;
        }
        if (a != null && a.ok() && !a.body.equals(alertKey)) {
            alertKey = a.body;
            JSONObject j = a.json();
            alertList = j == null ? null : j.optJSONArray("notifications");
            changed = true;
        }
        if (changed) render();
    }

    private void act(String path, String done) {
        final String base = Prefs.base(this), token = Prefs.token(this);
        io.execute(() -> {
            Api.Result r = Api.post(base, token, path);
            ui.post(() -> {
                Toast.makeText(this, r.ok() ? done : r.problem(), Toast.LENGTH_LONG).show();
                statusKey = "";
                poll();
            });
        });
    }

    // ---------------------------------------------------------------------------------------------- shell
    private void buildShell() {
        root.removeAllViews();
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setBackgroundColor(NAVY);
        top.setPadding(dp(18), dp(12), dp(18), dp(12));
        top.addView(text("ReviewMind Ops", 19, Color.WHITE, true));
        subtitle = text(Prefs.base(this).replace("http://", ""), 12, 0xFFB7C3DA, false);
        top.addView(subtitle);
        col.addView(top, new LinearLayout.LayoutParams(-1, -2));

        banner = text("", 13, Color.WHITE, false);
        banner.setBackgroundColor(RED);
        banner.setPadding(dp(18), dp(8), dp(18), dp(8));
        banner.setVisibility(View.GONE);
        col.addView(banner, new LinearLayout.LayoutParams(-1, -2));

        ScrollView sv = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(14), dp(14), dp(14), dp(24));
        sv.addView(content, new ViewGroup.LayoutParams(-1, -2));
        col.addView(sv, new LinearLayout.LayoutParams(-1, 0, 1f));

        tabs = new LinearLayout(this);
        tabs.setBackgroundColor(CARD);
        col.addView(tabs, new LinearLayout.LayoutParams(-1, dp(52)));
        root.addView(col, new FrameLayout.LayoutParams(-1, -1));
    }

    private void renderTabs() {
        tabs.removeAllViews();
        String[][] items = {{"services", "Services"}, {"alerts", "Alerts"}, {"settings", "Settings"}};
        for (String[] it : items) {
            boolean on = screen.equals(it[0]) || (screen.equals("incident") && it[0].equals("services"));
            TextView t = text(it[1], 14, on ? ACCENT : MUTED, on);
            t.setGravity(Gravity.CENTER);
            t.setOnClickListener(v -> {
                screen = it[0];
                incident = null;
                armed = false;
                if (it[0].equals("alerts")) { alertKey = ""; poll(); }
                render();
            });
            tabs.addView(t, new LinearLayout.LayoutParams(0, -1, 1f));
        }
    }

    private void render() {
        if (content == null) return;
        content.removeAllViews();
        renderTabs();
        switch (screen) {
            case "alerts": renderAlerts(); break;
            case "settings": renderSettings(); break;
            case "incident": renderIncident(); break;
            default: renderServices();
        }
    }

    // ------------------------------------------------------------------------------------------ connect
    private void showConnect(String message) {
        root.removeAllViews();
        content = null;
        banner = null;
        ScrollView sv = new ScrollView(this);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(22), dp(48), dp(22), dp(24));
        col.addView(text("ReviewMind Ops", 26, NAVY, true));
        col.addView(text("Connect to your control plane to see incidents, approve fixes and get alerts.", 14, MUTED, false), margin(0, 6, 0, 22));

        col.addView(text("Server address", 12, MUTED, true));
        EditText addr = field("192.168.1.5:9000", false);
        addr.setText(Prefs.base(this).replace("http://", ""));
        col.addView(addr, margin(0, 4, 0, 14));
        col.addView(text("Access token", 12, MUTED, true));
        EditText tok = field("paste it from the dashboard's Phone panel", true);
        col.addView(tok, margin(0, 4, 0, 6));
        col.addView(text("The laptop and this phone must be on the same Wi-Fi. The address is the laptop's IP, shown by ipconfig.", 12, MUTED, false), margin(0, 0, 0, 18));

        TextView result = text(message == null ? "" : message, 13, RED, false);
        Button go = button("Connect", ACCENT, true);
        go.setOnClickListener(v -> {
            String base = Prefs.normalize(addr.getText().toString());
            String token = tok.getText().toString().trim();
            if (base.isEmpty() || token.isEmpty()) {
                result.setTextColor(RED);
                result.setText("Enter both the address and the token.");
                return;
            }
            go.setEnabled(false);
            result.setTextColor(MUTED);
            result.setText("Connecting...");
            hideKeyboard(v);
            io.execute(() -> {
                Api.Result r = Api.get(base, token, "/m/api/status");
                ui.post(() -> {
                    go.setEnabled(true);
                    if (r.ok()) {
                        Prefs.save(this, base, token);
                        statusKey = "";
                        screen = "services";
                        start(null);
                    } else {
                        result.setTextColor(RED);
                        result.setText(r.problem());
                    }
                });
            });
        });
        col.addView(go, new LinearLayout.LayoutParams(-1, dp(48)));
        col.addView(result, margin(0, 14, 0, 0));
        sv.addView(col);
        root.addView(sv, new FrameLayout.LayoutParams(-1, -1));
    }

    // ----------------------------------------------------------------------------------------- services
    private void renderServices() {
        if (status == null) {
            content.addView(text("Loading...", 14, MUTED, false));
            return;
        }
        JSONObject set = status.optJSONObject("settings");
        if (set != null) {
            JSONObject can = set.optJSONObject("canary");
            String line = "Watchdog " + (set.optBoolean("watchdog") ? "on" : "off")
                    + "   |   Agents " + (set.optBoolean("agentic") ? "on" : "off")
                    + "   |   Canary " + (can != null && can.optBoolean("enabled") ? "on (" + can.optInt("percent") + "%)" : "off");
            JSONObject tr = set.optJSONObject("traffic");
            if (tr != null) line += "   |   Traffic " + (tr.optBoolean("enabled") ? "on" : "off");
            content.addView(text(line, 12, MUTED, false), margin(2, 0, 0, 10));
        }
        JSONArray list = status.optJSONArray("services");
        if (list == null) return;
        for (int i = 0; i < list.length(); i++) {
            JSONObject s = list.optJSONObject(i);
            final String name = s.optString("name");
            LinearLayout card = card();
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            TextView title = text(cap(s.optString("title", name)), 16, INK, true);
            row.addView(title, new LinearLayout.LayoutParams(0, -2, 1f));
            row.addView(pill(s.optString("status")));
            card.addView(row);
            String rs = repairLabel(s.optString("repair"));
            boolean pending = s.optString("repair").equals("pending_approval");
            if (!rs.isEmpty()) card.addView(text(rs, 13, pending ? ACCENT : MUTED, pending), margin(0, 6, 0, 0));
            if (pending) card.addView(text("Tap to review the fix", 12, ACCENT, false), margin(0, 2, 0, 0));
            card.setOnClickListener(v -> {
                screen = "incident";
                incident = name;
                armed = false;
                render();
            });
            content.addView(card, margin(0, 0, 0, 10));
        }
    }

    // ----------------------------------------------------------------------------------------- incident
    private JSONObject find(String name) {
        JSONArray list = status == null ? null : status.optJSONArray("services");
        if (list == null) return null;
        for (int i = 0; i < list.length(); i++) {
            JSONObject s = list.optJSONObject(i);
            if (s.optString("name").equals(name)) return s;
        }
        return null;
    }

    private void renderIncident() {
        JSONObject s = find(incident);
        TextView back = text("< All services", 14, ACCENT, true);
        back.setPadding(0, dp(2), 0, dp(10));
        back.setOnClickListener(v -> onBackPressed());
        content.addView(back);
        if (s == null) {
            content.addView(text("Loading...", 14, MUTED, false));
            return;
        }
        final String name = s.optString("name"), repair = s.optString("repair");

        LinearLayout head = card();
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(text(cap(s.optString("title", name)), 20, INK, true), new LinearLayout.LayoutParams(0, -2, 1f));
        row.addView(pill(s.optString("status")));
        head.addView(row);
        String detail = s.optString("detail");
        if (!detail.isEmpty()) head.addView(text(detail, 13, INK, false), margin(0, 8, 0, 0));
        String rl = repairLabel(repair);
        if (!rl.isEmpty()) head.addView(text(rl, 13, MUTED, true), margin(0, 8, 0, 0));
        String note = s.optString("note");
        if (!note.isEmpty() && !note.equals("null")) head.addView(text(note, 12, MUTED, false), margin(0, 4, 0, 0));
        JSONObject rb = s.optJSONObject("rollback");
        if (rb != null) head.addView(text("Rolled back: " + rb.optString("reason"), 12, AMBER, false), margin(0, 6, 0, 0));
        JSONObject pr = s.optJSONObject("pr");
        if (pr != null) head.addView(text("Incident PR #" + pr.optInt("number") + " (" + pr.optString("state") + ")", 12, MUTED, false), margin(0, 6, 0, 0));
        content.addView(head, margin(0, 0, 0, 12));

        JSONObject p = s.optJSONObject("proposal");
        if (p != null) {
            LinearLayout pc = card();
            pc.addView(text("Proposed fix", 15, INK, true));
            String how = p.optString("how", "");
            pc.addView(text((how.isEmpty() ? "" : how + "  |  ") + p.optInt("tokens") + " tokens", 12, MUTED, false), margin(0, 2, 0, 8));
            JSONArray steps = p.optJSONArray("agents");
            if (steps != null) {
                for (int i = 0; i < steps.length(); i++) {
                    JSONObject a = steps.optJSONObject(i);
                    TextView t = text(a.optString("agent") + ": " + a.optString("summary"), 13, INK, false);
                    t.setPadding(0, dp(3), 0, dp(3));
                    pc.addView(t);
                }
            }
            HorizontalScrollView hs = new HorizontalScrollView(this);
            TextView diff = new TextView(this);
            diff.setTypeface(Typeface.MONOSPACE);
            diff.setTextSize(11);
            diff.setTextColor(INK);
            diff.setText(colourDiff(p.optString("diff")));
            diff.setPadding(dp(8), dp(8), dp(8), dp(8));
            hs.setBackgroundColor(0xFFF8F9FB);
            hs.addView(diff);
            pc.addView(hs, margin(0, 8, 0, 0));
            if (repair.equals("pending_approval")) pc.addView(text("Nothing has been applied yet.", 12, MUTED, false), margin(0, 8, 0, 0));
            content.addView(pc, margin(0, 0, 0, 12));
        }

        if (repair.equals("pending_approval")) {
            Button ok = button(armed ? "Tap again to apply this fix" : "Approve and apply", armed ? RED : GREEN, true);
            ok.setOnClickListener(v -> {
                if (!armed) {
                    armed = true;
                    ui.removeCallbacks(disarm);
                    ui.postDelayed(disarm, 6000);
                    render();
                } else {
                    armed = false;
                    ui.removeCallbacks(disarm);
                    ok.setEnabled(false);
                    act("/m/api/approve/" + name, "Approved. Applying and verifying...");
                }
            });
            content.addView(ok, new LinearLayout.LayoutParams(-1, dp(50)));
            Button no = button("Reject", RED, false);
            no.setOnClickListener(v -> act("/m/api/reject/" + name, "Rejected. Nothing was changed."));
            content.addView(no, margin(0, 10, 0, 0, -1, dp(46)));
        } else if (p == null && !s.optString("status").equals("healthy") && repair.equals("idle")) {
            content.addView(text("No fix has been prepared yet. Use the dashboard on the laptop to start one.", 13, MUTED, false));
        }
    }

    private SpannableStringBuilder colourDiff(String diff) {
        SpannableStringBuilder sb = new SpannableStringBuilder();
        for (String line : diff.split("\n", -1)) {
            int start = sb.length();
            sb.append(line).append('\n');
            int end = sb.length();
            if (line.startsWith("+") && !line.startsWith("+++")) {
                sb.setSpan(new ForegroundColorSpan(0xFF146C3C), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                sb.setSpan(new BackgroundColorSpan(0xFFE3F5EA), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            } else if (line.startsWith("-") && !line.startsWith("---")) {
                sb.setSpan(new ForegroundColorSpan(0xFF9B2A25), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                sb.setSpan(new BackgroundColorSpan(0xFFFBE7E5), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            } else if (line.startsWith("@@")) {
                sb.setSpan(new ForegroundColorSpan(MUTED), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
        }
        return sb;
    }

    // -------------------------------------------------------------------------------------------- alerts
    private void renderAlerts() {
        if (alertList == null) {
            content.addView(text("Loading...", 14, MUTED, false));
            return;
        }
        if (alertList.length() == 0) {
            content.addView(text("No alerts yet. They appear here when a service breaks or a fix is ready.", 14, MUTED, false));
            return;
        }
        SimpleDateFormat fmt = new SimpleDateFormat("d MMM, HH:mm:ss", Locale.getDefault());
        for (int i = 0; i < alertList.length(); i++) {
            JSONObject n = alertList.optJSONObject(i);
            LinearLayout card = card();
            card.addView(text(n.optString("title"), 14, INK, true));
            card.addView(text(n.optString("body"), 13, MUTED, false), margin(0, 3, 0, 0));
            card.addView(text(fmt.format(new Date(Math.round(n.optDouble("t") * 1000))), 11, MUTED, false), margin(0, 6, 0, 0));
            final String svc = n.optString("svc", "");
            if (!svc.isEmpty()) {
                card.setOnClickListener(v -> {
                    screen = "incident";
                    incident = svc;
                    render();
                });
            }
            content.addView(card, margin(0, 0, 0, 10));
        }
    }

    // ------------------------------------------------------------------------------------------ settings
    private void renderSettings() {
        LinearLayout c1 = card();
        c1.addView(text("Server", 12, MUTED, true));
        c1.addView(text(Prefs.base(this), 15, INK, false), margin(0, 2, 0, 10));
        Button change = button("Change server or token", ACCENT, false);
        change.setOnClickListener(v -> showConnect(null));
        c1.addView(change, new LinearLayout.LayoutParams(-1, dp(44)));
        content.addView(c1, margin(0, 0, 0, 12));

        LinearLayout c2 = card();
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.addView(text("Alerts", 15, INK, true));
        labels.addView(text("Watch in the background and notify me", 12, MUTED, false));
        row.addView(labels, new LinearLayout.LayoutParams(0, -2, 1f));
        Switch sw = new Switch(this);
        sw.setChecked(Prefs.alerts(this));
        sw.setOnCheckedChangeListener((b, on) -> {
            Prefs.setAlerts(this, on);
            watchInBackground();
        });
        row.addView(sw);
        c2.addView(row);

        c2.addView(text("Check every", 12, MUTED, true), margin(0, 14, 0, 6));
        LinearLayout opts = new LinearLayout(this);
        int[] secs = {3, 5, 10, 30};
        for (int s : secs) {
            boolean on = Prefs.interval(this) == s;
            Button b = button(s + " s", on ? ACCENT : MUTED, on);
            b.setOnClickListener(v -> {
                Prefs.setInterval(this, s);
                watchInBackground();
                render();
            });
            opts.addView(b, new LinearLayout.LayoutParams(0, dp(40), 1f));
        }
        c2.addView(opts);
        c2.addView(text("A shorter wait means quicker alerts and slightly more battery.", 11, MUTED, false), margin(0, 6, 0, 0));
        content.addView(c2, margin(0, 0, 0, 12));

        LinearLayout c3 = card();
        c3.addView(text("Keep alerts reliable", 15, INK, true));
        c3.addView(text("Android may pause background apps to save battery. Allow this app to run in the background so alerts arrive with the screen off.", 12, MUTED, false), margin(0, 3, 0, 10));
        Button bat = button("Allow background running", ACCENT, false);
        bat.setOnClickListener(v -> {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm != null && pm.isIgnoringBatteryOptimizations(getPackageName())) {
                Toast.makeText(this, "Already allowed.", Toast.LENGTH_SHORT).show();
                return;
            }
            try {
                startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
            } catch (Exception e) {
                startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
            }
        });
        c3.addView(bat, new LinearLayout.LayoutParams(-1, dp(44)));
        content.addView(c3, margin(0, 0, 0, 12));

        Button out = button("Disconnect and forget this server", RED, false);
        out.setOnClickListener(v -> {
            stopService(new Intent(this, MonitorService.class));
            Prefs.clear(this);
            status = null;
            alertList = null;
            statusKey = alertKey = "";
            screen = "services";
            showConnect(null);
        });
        content.addView(out, new LinearLayout.LayoutParams(-1, dp(46)));
        content.addView(text("ReviewMind Ops 1.0", 11, MUTED, false), margin(0, 14, 0, 0));
    }

    // ------------------------------------------------------------------------------------------- helpers
    private static String repairLabel(String state) {
        switch (state) {
            case "generating": return "AI is preparing a fix";
            case "pending_approval": return "Fix waiting for your approval";
            case "approved": return "Approved, applying";
            case "canary": return "Canary release in progress";
            case "canary_failed": return "Canary failed: nothing was promoted";
            case "resolved": return "Fixed and verified";
            case "rejected": return "Fix rejected";
            case "rolled_back": return "Rolled back to the last known-good version";
            case "failed": return "Fix failed";
            default: return "";
        }
    }

    private static String cap(String s) {
        return s.isEmpty() ? s : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private TextView text(String t, int sp, int color, boolean bold) {
        TextView v = new TextView(this);
        v.setText(t);
        v.setTextSize(sp);
        v.setTextColor(color);
        if (bold) v.setTypeface(Typeface.DEFAULT_BOLD);
        return v;
    }

    private TextView pill(String status) {
        int c = status.equals("healthy") ? GREEN : (status.equals("restarting") || status.equals("degraded")) ? AMBER : RED;
        TextView v = text(status.isEmpty() ? "unknown" : status, 12, Color.WHITE, true);
        v.setPadding(dp(10), dp(3), dp(10), dp(3));
        GradientDrawable g = new GradientDrawable();
        g.setColor(c);
        g.setCornerRadius(dp(12));
        v.setBackground(g);
        return v;
    }

    private LinearLayout card() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(dp(16), dp(14), dp(16), dp(14));
        GradientDrawable g = new GradientDrawable();
        g.setColor(CARD);
        g.setCornerRadius(dp(12));
        g.setStroke(dp(1), LINE);
        l.setBackground(g);
        return l;
    }

    private Button button(String label, int color, boolean filled) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextSize(15);
        b.setStateListAnimator(null);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(10));
        if (filled) {
            g.setColor(color);
            b.setTextColor(Color.WHITE);
        } else {
            g.setColor(CARD);
            g.setStroke(dp(1), color);
            b.setTextColor(color);
        }
        b.setBackground(g);
        return b;
    }

    private EditText field(String hint, boolean secret) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setSingleLine(true);
        e.setTextSize(15);
        e.setTextColor(INK);
        e.setPadding(dp(12), dp(10), dp(12), dp(10));
        GradientDrawable g = new GradientDrawable();
        g.setColor(CARD);
        g.setCornerRadius(dp(8));
        g.setStroke(dp(1), LINE);
        e.setBackground(g);
        if (secret) e.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        else e.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        return e;
    }

    private LinearLayout.LayoutParams margin(int l, int t, int r, int b) {
        return margin(l, t, r, b, -1, -2);
    }

    private LinearLayout.LayoutParams margin(int l, int t, int r, int b, int w, int h) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private void hideKeyboard(View v) {
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) imm.hideSoftInputFromWindow(v.getWindowToken(), 0);
    }
}
