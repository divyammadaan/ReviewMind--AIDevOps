package com.reviewmind.aidevops;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Watches the control plane in the background and raises a real Android notification for each new alert.
 * It polls two small endpoints every few seconds; it needs no Google push service, so it works on any phone.
 * Tapping a notification only opens the app: nothing is ever approved from a notification.
 */
public class MonitorService extends Service {
    private static final String CH_WATCH = "watch", CH_ALERT = "alerts";
    private static final int ID_WATCH = 1;

    private volatile boolean running;
    private Thread worker;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel watch = new NotificationChannel(CH_WATCH, "Watching", NotificationManager.IMPORTANCE_LOW);
        watch.setDescription("Shows that the app is watching your services");
        NotificationChannel alert = new NotificationChannel(CH_ALERT, "Incident alerts", NotificationManager.IMPORTANCE_HIGH);
        alert.setDescription("A service broke, or a fix is waiting for your approval");
        nm.createNotificationChannel(watch);
        nm.createNotificationChannel(alert);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(ID_WATCH, watching("Starting..."));
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "monitor");
            worker.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    // ------------------------------------------------------------------------------------------------ loop
    private void loop() {
        while (running) {
            if (!Prefs.configured(this) || !Prefs.alerts(this)) {
                stopSelf();
                return;
            }
            try {
                cycle();
            } catch (Exception ignored) {
                // never let one bad answer stop the watcher
            }
            try {
                Thread.sleep(Math.max(3, Prefs.interval(this)) * 1000L);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private void cycle() throws Exception {
        String base = Prefs.base(this), token = Prefs.token(this);

        Api.Result st = Api.get(base, token, "/m/api/status");
        if (!st.ok()) {
            update("Cannot reach the control plane");
            return;
        }
        JSONObject status = st.json();
        update(summary(status));

        Api.Result nr = Api.get(base, token, "/m/api/notifications");
        if (!nr.ok() || nr.json() == null) return;
        JSONArray list = nr.json().optJSONArray("notifications");
        if (list == null) return;

        long seen = Prefs.seen(this), newest = seen;
        List<JSONObject> fresh = new ArrayList<>();
        for (int i = 0; i < list.length(); i++) {
            JSONObject n = list.getJSONObject(i);
            long t = Math.round(n.optDouble("t", 0) * 1000);
            newest = Math.max(newest, t);
            if (seen != 0 && t > seen) fresh.add(n);   // first run only sets the baseline: old alerts are not replayed
        }
        Collections.reverse(fresh);                      // oldest first
        for (JSONObject n : fresh) alert(n);
        if (newest != seen) Prefs.setSeen(this, newest);
    }

    private static String summary(JSONObject status) {
        JSONArray s = status.optJSONArray("services");
        int total = s == null ? 0 : s.length(), bad = 0;
        for (int i = 0; i < total; i++) {
            String st = s.optJSONObject(i).optString("status");
            if (!st.equals("healthy")) bad++;
        }
        JSONArray pending = status.optJSONArray("pending");
        int waiting = pending == null ? 0 : pending.length();
        if (waiting > 0) return waiting + (waiting == 1 ? " fix is" : " fixes are") + " waiting for your approval";
        if (bad > 0) return bad + (bad == 1 ? " service needs" : " services need") + " attention";
        return "All " + total + " services healthy";
    }

    // ------------------------------------------------------------------------------------- notifications
    private PendingIntent open(String svc) {
        Intent i = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (svc != null && !svc.isEmpty()) i.putExtra("svc", svc);
        return PendingIntent.getActivity(this, svc == null ? 0 : svc.hashCode(), i,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification watching(String text) {
        return new Notification.Builder(this, CH_WATCH)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("ReviewMind Ops")
                .setContentText(text)
                .setContentIntent(open(null))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build();
    }

    private String lastWatch = "";

    private void update(String text) {
        if (text.equals(lastWatch)) return;
        lastWatch = text;
        getSystemService(NotificationManager.class).notify(ID_WATCH, watching(text));
    }

    private void alert(JSONObject n) {
        String svc = n.optString("svc", "");
        Notification.Builder b = new Notification.Builder(this, CH_ALERT)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(n.optString("title", "Alert"))
                .setContentText(n.optString("body", ""))
                .setStyle(new Notification.BigTextStyle().bigText(n.optString("body", "")))
                .setContentIntent(open(svc))
                .setAutoCancel(true);
        int id = (n.optString("id", "") + svc).hashCode();
        getSystemService(NotificationManager.class).notify(id, b.build());
    }
}
