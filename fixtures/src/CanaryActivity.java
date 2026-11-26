package com.androidlab.fixture;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Display;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

public final class CanaryActivity extends Activity {
    private static final int LOG_LIMIT_BYTES = 64 * 1024;

    private TextView status;
    private TextView counter;
    private TextView touch;
    private TextView observedText;
    private int count;
    private int focusLosses;
    private int pauses;
    private int imeChanges;
    private boolean imeVisible;
    private float gestureX;
    private float gestureY;
    private String marker = "UNSET";
    private SharedPreferences preferences;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        String requestedMarker = getIntent().getStringExtra("lab_marker");
        if (requestedMarker != null && requestedMarker.matches("[A-Za-z0-9-]{1,40}")) {
            marker = requestedMarker;
        }
        preferences = getSharedPreferences("canary", MODE_PRIVATE);
        count = preferences.getInt("count", 0);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN
                | WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(16), dp(16), dp(16), dp(16));
        column.setFocusableInTouchMode(true);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(column);
        setContentView(scroll);

        String role = getPackageName().equals("com.androidlab.canary.primary.lab") ? "PRIMARY" : "AUTOMATION";
        TextView heading = addText(column, "ANDROID LAB " + role + " CANARY");
        heading.setTextSize(22);
        addText(column, "Marker: " + marker);
        status = addText(column, "");
        addText(column, "Synthetic inputs only");
        counter = addText(column, "Count: " + count);

        Button increment = new Button(this);
        increment.setText("Increment");
        increment.setAllCaps(false);
        increment.setOnClickListener(view -> {
            int next = count + 1;
            if (preferences.edit().putInt("count", next).commit()) {
                count = next;
                counter.setText("Count: " + count);
                record("increment", "count", count);
            } else {
                counter.setText("Persistence error");
                record("persistence_error", "count", count);
            }
        });
        column.addView(increment);

        EditText input = new EditText(this);
        input.setSingleLine();
        input.setHint("Synthetic text");
        column.addView(input);
        observedText = addText(column, "Text observed: ");
        input.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence text, int start, int count, int after) {}

            @Override
            public void onTextChanged(CharSequence text, int start, int before, int count) {
                String value = bounded(text.toString());
                observedText.setText("Text observed: " + value);
                record("text", "value", value);
            }

            @Override
            public void afterTextChanged(Editable text) {}
        });

        touch = addText(column, "Gesture: none");
        TextView gesturePad = addText(column, "Gesture pad");
        gesturePad.setMinHeight(dp(112));
        gesturePad.setBackgroundColor(0xFFE2EDF6);
        gesturePad.setOnTouchListener((view, event) -> {
            if (event.getActionMasked() == MotionEvent.ACTION_UP) {
                view.performClick();
            }
            return true;
        });
        if (Build.VERSION.SDK_INT >= 30) {
            column.setOnApplyWindowInsetsListener((view, insets) -> {
                boolean visible = insets.isVisible(WindowInsets.Type.ime());
                if (imeVisible != visible) {
                    imeVisible = visible;
                    imeChanges++;
                    record("ime", "visible", visible);
                    updateStatus();
                }
                return insets;
            });
        }
        column.requestFocus();
        updateStatus();
        record("create", "count", count);
    }

    private TextView addText(LinearLayout column, String text) {
        TextView item = new TextView(this);
        item.setPadding(0, dp(4), 0, dp(4));
        item.setText(text);
        item.setTextSize(16);
        column.addView(item);
        return item;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @SuppressWarnings("deprecation")
    private int actualDisplay() {
        Display display = Build.VERSION.SDK_INT >= 30 ? getDisplay() : getWindowManager().getDefaultDisplay();
        return display == null ? Display.INVALID_DISPLAY : display.getDisplayId();
    }

    private void updateStatus() {
        if (status != null) {
            status.setText("Display: " + actualDisplay()
                    + "\nFocus losses: " + focusLosses + ", pauses: " + pauses
                    + "\nIME visible: " + imeVisible + ", changes: " + imeChanges);
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            gestureX = event.getX();
            gestureY = event.getY();
            record("touch_down", "points", Math.round(gestureX) + "," + Math.round(gestureY));
        } else if (action == MotionEvent.ACTION_UP && touch != null) {
            double distance = Math.hypot(event.getX() - gestureX, event.getY() - gestureY);
            String kind = distance > dp(20) ? "swipe" : "tap";
            String points = Math.round(gestureX) + "," + Math.round(gestureY) + " -> "
                    + Math.round(event.getX()) + "," + Math.round(event.getY());
            touch.setText("Gesture: " + kind + " (" + points + ")");
            record(kind, "points", points);
        } else if (action == MotionEvent.ACTION_CANCEL) {
            record("touch_cancel", "points", "");
        }
        return super.dispatchTouchEvent(event);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            record("key", "code", event.getKeyCode());
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (!hasFocus) {
            focusLosses++;
        }
        record("focus", "focused", hasFocus);
        updateStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
        record("resume", "count", count);
    }

    @Override
    protected void onPause() {
        pauses++;
        updateStatus();
        record("pause", "pauses", pauses);
        super.onPause();
    }

    @Override
    protected void onStop() {
        record("stop", "count", count);
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        record("destroy", "count", count);
        super.onDestroy();
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        record("back", "count", count);
        super.onBackPressed();
    }

    private String bounded(String text) {
        int points = text.codePointCount(0, text.length());
        return points <= 128 ? text : text.substring(0, text.offsetByCodePoints(0, 128));
    }

    private void record(String event, String key, Object value) {
        try {
            JSONObject row = new JSONObject()
                    .put("event", event)
                    .put("uptime_ms", SystemClock.uptimeMillis())
                    .put("display", actualDisplay())
                    .put("orientation", getResources().getConfiguration().orientation)
                    .put("marker", marker)
                    .put(key, value);
            byte[] bytes = (row.toString() + "\n").getBytes(StandardCharsets.UTF_8);
            File file = new File(getFilesDir(), "canary-events.jsonl");
            if (file.length() + bytes.length <= LOG_LIMIT_BYTES) {
                try (FileOutputStream output = new FileOutputStream(file, true)) {
                    output.write(bytes);
                }
            } else {
                setTitle("Canary event log full");
            }
        } catch (IOException | JSONException error) {
            setTitle("Canary event log unavailable");
        }
    }
}
