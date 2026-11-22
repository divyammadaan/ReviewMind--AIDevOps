package com.reviewmind.aidevops;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

import org.json.JSONObject;

/** Tiny HTTP client for the control plane's token-protected phone API (/m/api/...). */
final class Api {
    private Api() {}

    static final class Result {
        int code;
        String body = "";
        String error;   // set when the server could not be reached at all

        boolean ok() { return error == null && code >= 200 && code < 300; }

        JSONObject json() {
            try { return new JSONObject(body); } catch (Exception e) { return null; }
        }

        /** A sentence a person can act on. */
        String problem() {
            if (error != null) return "Cannot reach the control plane (" + error + "). Is the laptop on the same Wi-Fi?";
            if (code == 401) return "The access token was refused. Copy it again from the dashboard's Phone panel.";
            JSONObject j = json();
            if (j != null && j.has("detail")) return j.optString("detail");
            return "The server answered " + code + ".";
        }
    }

    static Result get(String base, String token, String path) { return call(base, token, "GET", path, 6000); }
    static Result post(String base, String token, String path) { return call(base, token, "POST", path, 15000); }

    private static Result call(String base, String token, String method, String path, int timeoutMs) {
        Result r = new Result();
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + path).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setRequestProperty("Authorization", "Bearer " + token);
            if (method.equals("POST")) {
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(0);
            }
            r.code = c.getResponseCode();
            InputStream in = r.code >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in != null) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
                r.body = out.toString("UTF-8");
                in.close();
            }
        } catch (Exception e) {
            String m = e.getClass().getSimpleName();
            r.error = m.isEmpty() ? "network error" : m;
        } finally {
            if (c != null) c.disconnect();
        }
        return r;
    }
}
