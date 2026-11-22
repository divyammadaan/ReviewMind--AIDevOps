package com.reviewmind.aidevops;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** After a reboot, start watching again (if the app was set up and alerts are on). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) && Prefs.configured(context) && Prefs.alerts(context)) {
            try {
                context.startForegroundService(new Intent(context, MonitorService.class));
            } catch (Exception ignored) {
                // the system may refuse; the app starts watching again the next time it is opened
            }
        }
    }
}
