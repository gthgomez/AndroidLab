package com.androidlab.fixture;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;

public final class StorageTest extends Instrumentation {
    private static final String TARGET = "com.androidlab.canary.automation.lab";

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override
    public void onStart() {
        Bundle result = new Bundle();
        int code = Activity.RESULT_CANCELED;
        Context target = getTargetContext();
        if (!TARGET.equals(target.getPackageName())) {
            result.putString("stream", "\nFAILURES!!! Unexpected fixture target.\n");
        } else {
            SharedPreferences preferences = target.getSharedPreferences("instrumentation", Context.MODE_PRIVATE);
            SharedPreferences previous = target.getSharedPreferences("canary", Context.MODE_PRIVATE);
            int count = previous.getInt("count", 0);
            boolean committed = preferences.edit().putString("fixture", "SYNTHETIC").commit();
            if (committed && "SYNTHETIC".equals(preferences.getString("fixture", null))
                    && count == previous.getInt("count", 0)) {
                result.putString("stream", "\nOK (1 test)\n");
                code = Activity.RESULT_OK;
            } else {
                result.putString("stream", "\nFAILURES!!! Synthetic storage assertion failed.\n");
            }
        }
        finish(code, result);
    }
}
