// Detects whether this browser can act as a BLE anchor, and says why not if it can't.
// Kept free of DOM access so it can be tested in Node with a fake environment.

export function describePlatform(userAgent = "") {
  const ua = userAgent.toLowerCase();
  const ios = /iphone|ipad|ipod/.test(ua) || (/macintosh/.test(ua) && /mobile/.test(ua));
  return {
    ios,
    android: /android/.test(ua),
    firefox: /firefox|fxios/.test(ua),
    samsung: /samsungbrowser/.test(ua),
    chromium: /chrome|chromium|crios|edg\//.test(ua) && !/fxios/.test(ua),
  };
}

/**
 * Returns { canScan, checks: [{id, ok, label}], problem: {title, steps[]} | null }.
 * `env` defaults to the page globals; tests pass a fake.
 */
export async function detectBleSupport(env = globalThis) {
  const nav = env.navigator || {};
  const platform = describePlatform(nav.userAgent || "");
  const origin = (env.location && env.location.origin) || "this address";
  const secure = Boolean(env.isSecureContext);
  const bluetooth = nav.bluetooth;
  const scanning = Boolean(bluetooth && typeof bluetooth.requestLEScan === "function");
  let available = null;
  if (bluetooth && typeof bluetooth.getAvailability === "function") {
    try {
      available = await bluetooth.getAvailability();
    } catch {
      available = null;
    }
  }

  const checks = [
    { id: "secure", ok: secure, label: "Page allowed to use Bluetooth (secure context)" },
    { id: "webbluetooth", ok: Boolean(bluetooth), label: "Web Bluetooth available" },
    { id: "scanning", ok: scanning, label: "Advertisement scanning available (requestLEScan)" },
    { id: "radio", ok: available, label: available === null ? "Bluetooth adapter (browser can't tell)" : "Bluetooth adapter present" },
  ];

  const flags = "chrome://flags";
  let problem = null;
  if (platform.ios) {
    problem = {
      title: "iPhone and iPad browsers can't scan for Bluetooth devices.",
      steps: [
        "Safari and every other iOS browser lack Web Bluetooth scanning.",
        "Use a Windows laptop as this anchor instead (Start anchor.bat, or start_anchor.bat from source).",
      ],
    };
  } else if (platform.firefox) {
    problem = {
      title: "Firefox doesn't support Web Bluetooth.",
      steps: ["Open this page in Chrome on Android, or use a Windows laptop as this anchor."],
    };
  } else if (!secure) {
    problem = {
      title: "Chrome only allows Bluetooth on secure pages, and this page is plain HTTP on your network.",
      steps: [
        `Open ${flags}/#unsafely-treat-insecure-origin-as-secure, enable it and enter ${origin}`,
        `In the same page enable ${flags}/#enable-experimental-web-platform-features`,
        "Tap Relaunch, then reopen this page.",
      ],
    };
  } else if (!bluetooth) {
    problem = {
      title: "This browser has no Web Bluetooth.",
      steps: ["Use Chrome on Android, or use a Windows laptop as this anchor."],
    };
  } else if (!scanning) {
    problem = {
      title: "This browser can't listen to Bluetooth advertisements yet.",
      steps: platform.android
        ? [`Open ${flags}/#enable-experimental-web-platform-features, enable it and tap Relaunch.`, "Reopen this page."]
        : [
            "Advertisement scanning in desktop browsers is experimental and not available on Windows.",
            "Run the laptop anchor app instead (Start anchor.bat, or start_anchor.bat from source).",
          ],
    };
  } else if (available === false) {
    problem = {
      title: "No Bluetooth adapter is available to this browser.",
      steps: ["Turn Bluetooth on, and on Android also turn on Location.", "Reload this page."],
    };
  }
  return { canScan: problem === null, checks, problem, platform };
}
