# BLE Locate

BLE Locate estimates where a phone (**T**) is in a room from the Bluetooth signal
strength (RSSI) that three anchors hear. **P1** is a Windows laptop that hosts the
dashboard and is also an anchor; **P2** and **P3** are Windows laptops or, where
the browser allows it, Android phones.

Every distance and position it shows is an **estimate** from RSSI. Indoors,
expect errors of roughly one to a few metres; bodies, walls, furniture and the
way the phone is held all change the signal. See [Accuracy](#accuracy).

## How it works

```
 T (nRF Connect advertising the target UUID)
   |  BLE advertisements + scan responses
   v
 P1, P2, P3 scanners (continuous active scan)
   |  only callbacks that match the target; each reading carries its age
   v
 P1 hub: freshness check -> rolling median -> time-aware Kalman
         -> path-loss model (distance estimate) -> height correction
         -> least-squares trilateration (only when all three are fresh)
   |
   v
 Dashboard in any browser on the LAN (laptops and phones)
```

- **P1** runs `hub`: the web dashboard (TCP 8780), the anchor and dashboard
  WebSocket links, the LAN discovery responder (UDP 8781), the shared
  configuration, and its own scanner as anchor P1.
- **P2/P3 laptops** run `anchor`: they broadcast a discovery probe, connect to
  whichever P1 answers, receive the shared configuration, scan continuously
  and stream readings. No keys or pairing; `--hub` is the manual fallback.
- **Phones** can be anchors from the dashboard's Connect tab when their browser
  supports Bluetooth advertisement scanning (see [Phones as anchors](#phones-as-anchors)).

T is identified by any combination of a service UUID, an exact advertised name and a
Bluetooth address; every part that is filled in must match. The matcher handles split advertisements. The UUID may arrive in the
advertising packet and the name only in the scan response. Once an address has
matched, it keeps its identity on later callbacks that carry neither. The match
is cleared when the target settings change, or when the address is unheard for
the "forget" time (default 30 s).

Readings are only created by real scanner callbacks. P1 timestamps each one on
its own clock from the age the anchor reports, which avoids clock-sync problems.
It rejects readings that are already stale, out of order, or meant for an older
target, and never uses stale readings for distance or position.

## What you need

- Three Windows 10/11 laptops with Bluetooth LE (Bluetooth 4.0 or newer), on
  the same Wi-Fi or wired LAN. P2/P3 may instead be phones with a supported browser.
- T: a smartphone with **nRF Connect for Mobile** (Nordic Semiconductor).
- On each laptop that runs from source: Python 3.10–3.14, 64-bit (3.13
  recommended). With the packaged app, only the build machine needs Python.

## Quick start (from source)

1. Copy this folder to each laptop.
2. Double-click **`setup.bat`** on each laptop. It creates `.venv`, installs
   Bleak and the PyWinRT Bluetooth modules, and runs a self-test.
3. On **P1**: double-click **`firewall_setup.bat`** once (it asks for administrator
   rights), then **`start_hub.bat`**. The dashboard opens at `http://localhost:8780/`.
4. On **P2** and **P3**: double-click **`start_anchor.bat`** and type `P2` or `P3`.
   It finds P1 by itself. If it can't, run `start_anchor.bat --anchor P2 --hub <P1 address>`.
   The address is shown in P1's console and on the dashboard's Connect tab.
5. Set up T (next section).
6. In the dashboard: **Settings** for the room size, anchor positions and
   heights; then **Calibrate** each anchor.

## Set up T in nRF Connect

On the phone that will be T (Android shown; menus vary slightly between app versions):

1. Open nRF Connect, go to the **Advertiser** tab, and add a new advertising packet.
2. Under **Advertising data**, add a **Service UUID** record with the
   128-bit UUID `12345678-1234-5678-1234-56789abcdef0` (or your own; it must
   match **Settings > Target > Service UUID** exactly).
3. Optional name: add **Complete Local Name** under **Scan response data**, not
   advertising data. A 128-bit UUID plus the flags already uses 21 of the 31 bytes,
   and most device names don't fit next to it. The advertised name is the phone's
   Bluetooth name. To filter on it, enter it exactly in **Settings > Target > Advertised name**.
4. Choose the shortest interval (often called low latency) and high TX power,
   make it scannable, and switch the advertiser on. Keep the phone awake and out
   of battery saver.
5. Check from any laptop: `selftest.bat` listens for 10 s and reports whether the
   target UUID was heard.

## The dashboard

Open it on P1 or on any device on the same network (Connect tab: address + QR code).

**Live** shows the room plan with the anchors, their estimated distance circles
and the estimated position of T, plus one panel per anchor:

| Shown | Meaning |
|---|---|
| Connected to P1 / Not connected to P1 | Whether that anchor's link to P1 is up. |
| Scanner stopped | The anchor is connected but not scanning (Bluetooth off, error, phone page hidden). The panel shows the error. |
| Target not seen | Scanning, but no matching callback yet (or not for longer than the forget time). |
| No fresh sample | T was heard, but the newest reading is older than the freshness limit (default 3 s). The last value is shown as "not fresh". |
| Fresh measurement | A reading arrived within the freshness limit; RSSI and distance estimate are current. |

Each panel also shows scanner status, target match (with address), last-seen
age, raw and smoothed RSSI, sample count, the distance estimate (straight-line
and along the floor), the device and whether it has the current settings.

A position appears only when **all three** anchors have fresh measurements.
Otherwise the plan says *No position estimate* and lists which anchor is
waiting and why. If the dashboard loses its link to P1, a red banner says the
page is frozen and the readings are greyed out.

**Settings** edits the one shared configuration on P1: room size, anchor
coordinates, heights and RSSI at 1 m, target UUID/name/address/height, path-loss
exponent, smoothing, and freshness times. Saving pushes it to every anchor; each
panel shows when its anchor has confirmed. If two people edit at once, the second
save is refused and the latest version is loaded instead of silently
overwriting. **Export settings** and **Import settings** save and restore it as JSON.

Coordinates are metres from the room's lower-left corner (0, 0), x to the right
and y up. Heights are those of each anchor's Bluetooth adapter and of T above
the floor; they turn straight-line distances into floor distances.

**Devices** lists every Bluetooth device the laptop anchors hear, so you can
pick any of them as T with **Track**. BLE is noisy, so the list is built to
hold still:

- Rows never move by themselves. The first view is ordered by signal; after
  that new devices are added at the bottom. Only **Sort by signal** (or Track)
  reorders, and Sort also removes devices that have left.
- A device appears only after it has been heard a few times, so one-off blips
  from far away stay out.
- Signal is a per-anchor moving average of one-second medians. The dBm figure
  only changes when it moves by 2 dB or more, and the bars only when the signal
  is clearly past a level boundary.
- A device that goes quiet for 15 s fades in place ("Last heard 40 s ago")
  instead of disappearing; its old values are not shown as current. After
  5 minutes it is dropped.
- **Hide unnamed devices** and the filter box cut the list down without
  reordering it. Unnamed devices show their maker when they advertise one
  ("Unnamed Apple device").

Track picks the most stable way to recognise the device. If it advertises a
custom 128-bit service UUID, Track uses that (plus its name). Otherwise it uses
the name, and only as a last resort the address. Name matching counts any
device advertising exactly that name. Phones, earbuds and watches usually
change their address every few minutes, so tracking by address breaks then, and
phone anchors can't match an address at all. The tracked device is pinned to the
top and marked *Tracking*; the Live tab locates it from then on. Only laptop
anchors (including P1's own scanner) contribute to the list: a browser hides
real Bluetooth addresses, so phone anchors can't.

**Calibrate** measures each anchor's *RSSI at 1 m*: put T at a measured
distance from the anchor (1–2 m, clear line of sight), collect readings for
about 15 s, and apply. Do this for every anchor, because adapters differ by
several dB.

## Phones as anchors

Phone scanning uses the Web Bluetooth Scanning API (`requestLEScan`). In
Chrome this API is still experimental and must be switched on; it is not
available in Safari or any iPhone/iPad browser, or in Firefox. The Connect tab
detects all of this and explains what is missing before offering scanning.

On Android Chrome:

1. Open `chrome://flags`, enable **Experimental Web Platform features**, and
   enable **Insecure origins treated as secure** with P1's address (for
   example `http://192.168.1.10:8780`). Chrome allows Bluetooth only on secure
   pages, and the dashboard is plain HTTP on your network. Relaunch Chrome.
2. Turn on Bluetooth and Location, open the dashboard, go to **Connect**, pick
   the anchor role and tap **Start scanning**. Allow the Bluetooth prompt.
3. Keep the page open with the screen on. Chrome stops scanning in the
   background; the panel then shows *Scanner stopped* (paused) and you tap
   **Resume scanning**. The page asks to keep the screen awake while scanning.

Laptops are the dependable choice: the Windows anchor scans continuously in the background.

## Accuracy

- RSSI fluctuates by several dB from moment to moment; the median and Kalman
  filter smooth this but add a little lag. Increase *Kalman measurement noise*
  for steadier readings, or *process noise* to follow movement faster.
- The path-loss exponent is about 2 in open space and 2.5–4 with obstacles.
  Calibrating each anchor matters more than the exponent.
- Place anchors around the room's edges, not in a line, with line of sight to
  T where possible. A person between T and an anchor can cost 5–10 dB.
- The "circles disagree by" figure under the plan is the fit's mismatch, not
  the true error; a small mismatch can still be a wrong position.

## Build the Windows executable

On a Windows machine with this folder (run `setup.bat` first):

```
build_exe.bat
```

It installs PyInstaller (pinned in `requirements-build.txt`), checks the scanner
loads from `.venv`, and builds `dist\BLELocate\` (`BLELocate.exe` plus
`_internal\`). Then it confirms `winrt\_winrt.*.pyd` is in the bundle and runs
`BLELocate.exe selftest --require-windows-scanner`. That self-test imports
`winrt._winrt` and every Bluetooth namespace from inside the bundle and creates
Bleak's WinRT scanner; the build fails if any of it is missing. Finally it adds
the launchers and docs and writes `dist\BLELocate-<version>-win-<arch>.zip`.

PyWinRT is collected from what its installed packages recorded, not from
imports, by `packaging/pywinrt_collect.py`. That covers the native modules
(`_winrt*.pyd`), the `msvcp140.dll` they load from their own folder, and, for
PyWinRT 4.x, the `_table.pywinrt` data files.

Build on the same architecture as the laptops (x64 for x64, ARM64 for ARM64).

## Deploy to P1, P2 and P3

1. Copy the zip to each laptop and unzip it, for example to `C:\BLELocate`.
   Keep `_internal` next to `BLELocate.exe`.
2. **P1**: run `Firewall setup (P1 only).bat` once, then `Start P1 hub.bat`.
3. **P2/P3**: run `Start anchor.bat` and type the role.
4. Optional on each laptop: `Self-test.bat` checks the adapter and listens for T for 10 s.

The launchers only run the `BLELocate.exe` in their own folder, never a Python
from PATH. The source launchers likewise only use `.venv`.

**P1's configuration is preserved across updates.** It lives outside the app
folder, in `%APPDATA%\BLELocate\config.json`; logs are in
`%APPDATA%\BLELocate\logs\`. To update:

1. On the dashboard, **Settings > Export settings** (a backup).
2. Close the BLE Locate windows on all laptops.
3. Replace the app folder with the new one (delete the old folder or unzip over it).
4. Start P1 again. It reads the same `config.json`; anchors get it when they reconnect.

Anchors keep no configuration of their own, so replacing their folder loses
nothing. To move P1 to a different laptop, copy `config.json` into
`%APPDATA%\BLELocate\` on the new P1, or use **Import settings**. Firewall
rules are tied to the ports, not the folder, so they survive updates. If the
Windows firewall prompt appears again after moving the folder, choose
**Private networks** and **Allow**.

## Command line

```
BLELocate.exe hub      [--port 8780] [--discovery-port 8781] [--no-scan] [--no-browser] [--data-dir DIR]
BLELocate.exe anchor   [--anchor P2|P3] [--hub ADDRESS[:PORT]] [--no-browser]
BLELocate.exe selftest [--require-windows-scanner] [--no-adapter] [--scan SECONDS] [--target UUID]
```

From source, replace `BLELocate.exe` with `.venv\Scripts\python.exe -m blelocate`.
Run without arguments, the exe shows a small menu. If you change the ports,
rerun the firewall script with `-HttpPort`/`-DiscoveryPort`, and use the
same `--discovery-port` on the anchors.

## Security

There are no per-anchor keys, by design: anyone on the same network can open
the dashboard, change settings or connect as an anchor. Use it on a network you
trust. The firewall rules only accept connections from P1's own subnet and
only on Private/Domain networks.

## Development

```
python -m pip install -r requirements-dev.txt
python -m pytest            # Python: matching, filters, freshness, discovery, config sync, packaging
npm test                    # Node 22+: browser matcher (same vectors as Python), support detection, phone anchor
```

`tests/vectors/matching.json` holds the matching cases both implementations
must pass. The hub tests run P1 and anchors over real HTTP/WebSocket
connections with fake Bluetooth scanners.

```
blelocate/        Python package: hub, anchor, scanner, matcher, filters, engine, discovery, config
blelocate/web/    dashboard (plain HTML/CSS/JS modules, no build step)
packaging/        PyInstaller spec, PyWinRT collector, launchers for the packaged folder
scripts/          setup, build and firewall PowerShell scripts (run via the .bat files)
tests/            pytest and node:test suites
```
