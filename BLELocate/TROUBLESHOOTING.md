# Troubleshooting

Start here on any laptop that misbehaves:

- **Self-test**: `selftest.bat` (source) or `Self-test.bat` (packaged). It checks
  the Bluetooth support files, the adapter and radio, then listens for 10 s and
  reports whether T's UUID was heard. Every `[FAIL]` line says what to do next.
- **Logs**: `%APPDATA%\BLELocate\logs\hub.log` on P1 and `anchor.log` on anchors.
  Paste `%APPDATA%\BLELocate` into the Explorer address bar to open the folder.
- **The anchor's panel on the dashboard** shows its status and the latest
  scanner error.

## Setup

| Problem | Fix |
|---|---|
| "No suitable Python found" | Install Python 3.13 64-bit from python.org with the *py launcher* option, then run `setup.bat` again. To use a specific Python: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Python C:\Path\python.exe` |
| Typing `python` opens the Microsoft Store | That is a Windows shortcut, not Python. Setup ignores it; install Python as above. |
| "pip could not install the dependencies" | The laptop needs internet access for setup. Behind a proxy, set `HTTPS_PROXY` first, or run setup on another network. |
| "The existing .venv is broken" | Python was moved or uninstalled. Setup recreates `.venv` automatically; you can also delete the `.venv` folder and run setup again. |
| "running scripts is disabled on this system" | Start through the `.bat` files; they pass `-ExecutionPolicy Bypass` for that one script. |
| "The Python environment is missing" from a start script | Run `setup.bat` in the same folder first. The start scripts only use `.venv` in their own folder. |
| Self-test: `winrt... failed to import` | Delete `.venv` and run `setup.bat` again. In the packaged app: re-copy the whole folder including `_internal`. Antivirus may have quarantined a `.pyd`; restore it and add an exclusion. |

## Wi-Fi and network

Anchors find P1 by broadcasting on the local network. They must be on the **same
network and subnet** as P1.

- **"Looking for P1 on this network..." never ends**
  1. Check that P1 is running (its console shows "Dashboard for other devices: http://...").
  2. Check that both laptops are on the same Wi-Fi. *Guest* networks and many
     school/office networks isolate devices from each other; use a home
     router or a phone hotspot instead.
  3. Disconnect VPNs on both laptops.
  4. Start the anchor with P1's address: `start_anchor.bat --anchor P2 --hub 192.168.1.10`
     (packaged: `"Start anchor.bat" --anchor P2 --hub 192.168.1.10`).
  5. If the manual address also fails, it's the firewall or the network, not
     discovery. In a browser on the anchor laptop, open
     `http://<P1 address>:8780/`. If the page does not load, see Windows Firewall below.
- **P1 shows several addresses**: the first one is the one Windows uses for
  outbound traffic. Addresses of virtual adapters (Hyper-V, VirtualBox, WSL,
  VPN) are listed last; use the address of the Wi-Fi/Ethernet adapter.
- **P1's address changes from day to day**: discovery copes with this. For the
  manual fallback, give P1 a fixed address (DHCP reservation) in the router.
- **"Automatic discovery is off: UDP port 8781 is busy"**: another program uses
  that port (often a second BLE Locate). Close it, or start P1 and the anchors
  with the same `--discovery-port`.

## Windows Firewall (P1)

Anchors and phones open connections *to* P1, so only P1 needs firewall rules.
Anchors need none: replies to their own discovery probe are allowed.

1. Run `firewall_setup.bat` (packaged: `Firewall setup (P1 only).bat`) and
   accept the administrator prompt. It allows TCP 8780 and UDP 8781 from P1's
   subnet on Private/Domain networks.
2. If it reports **BLOCK rules** for `python.exe` or `BLELocate.exe`, let it
   remove them. Windows creates them when someone clicks *Cancel* on its own
   "allow access" prompt, and a Block rule always beats an Allow rule.
3. If it reports that the Wi-Fi is **Public**, make it Private (Settings >
   Network & internet > Wi-Fi > *your network* > Network profile type: Private).
   Use `-AllowPublic` only if you can't, and only on a network you trust.
4. Devices on another subnet (some campus networks): rerun with `-AnySource`.
5. Check from an anchor in PowerShell: `Test-NetConnection <P1 address> -Port 8780`
   should say `TcpTestSucceeded : True`.

Third-party security suites (Norton, McAfee, ESET, ...) have their own firewall
and need the same ports allowed there.

## Bluetooth on the laptops

| Panel or console message | What to do |
|---|---|
| Bluetooth is turned off | Settings > Bluetooth & devices > Bluetooth on. Airplane mode turns it off too. |
| No Bluetooth adapter was found | Device Manager > Bluetooth: the adapter must be present without a warning sign. Update or reinstall its driver, or use a USB Bluetooth 4.0+ dongle. |
| This adapter cannot scan for BLE devices | The adapter is Bluetooth Classic only. Use a Bluetooth 4.0+ (LE) adapter. |
| The Bluetooth adapter is not ready | It was just switched on, unplugged, or is resetting. It retries every 5 s by itself. |
| Bluetooth access was denied | Settings > Privacy & security > Radios: allow apps to control device radios. |
| "No advertisements from any device ... restarted the scanner" | Normal after 20 s of total silence; if it repeats, Bluetooth may be off or the adapter is sleeping. In Device Manager > the adapter > Power Management, untick "Allow the computer to turn off this device". |
| Windows Bluetooth threading error | Restart BLE Locate. If it repeats, send `anchor.log`/`hub.log`. |

With two Bluetooth adapters (built-in plus dongle), Windows uses one of them.
Disable the other in Device Manager if scanning is unreliable.

## T is not seen ("Target not seen")

1. In nRF Connect on T, the advertiser must be **on** (toggle switched) and the
   phone awake. Battery saver can stop advertising.
2. The **Service UUID** record must contain exactly the UUID in Settings >
   Target. A typo in one digit means no match. The dashboard accepts upper or
   lower case and 16-bit short forms (`180F`).
3. If **Advertised name** is set in Settings, it must match T's advertised name
   exactly (case, spaces). Clear the field to match on UUID alone.
4. If nRF Connect reports that the data is too large, move **Complete Local
   Name** from *Advertising data* to *Scan response data*. BLE Locate joins the
   two halves.
5. Run the self-test near T (`selftest.bat`; add `--target <your UUID>` if you
   changed it from the default). It should report that the target was heard. If other devices are heard but not T, the problem is T's
   advertiser settings.
6. Android phones change their Bluetooth address every few minutes. BLE Locate
   re-identifies T from the UUID automatically; a gap of a second or two at
   that moment is normal.

On iPhone, nRF Connect can advertise only while the app is in the foreground,
and iOS may leave out the name. Keep the app open, and match on the UUID only.

## Dashboard states

| State | Usual cause | Fix |
|---|---|---|
| Not connected to P1 | Anchor app not running, or network/firewall blocks it | Start the anchor; see Network and Firewall above |
| Scanner stopped | Bluetooth off or failing; phone page hidden | Read the error line in the panel; on phones tap *Resume scanning* |
| Target not seen | T not advertising or settings don't match | See "T is not seen" above |
| No fresh sample | T out of range, blocked, or advertising too slowly | Move closer, shorten T's advertising interval, or raise *Reading counts as fresh for* |
| Fresh but no position | Anchors lie on one line | Move one anchor off the line in the room and in Settings |
| "Settings: waiting for the anchor to confirm" | The anchor hasn't applied the newest settings | It updates within a second; if it stays, restart that anchor |
| "Last rejected reading: arrived already stale" | The network delays readings by more than the freshness limit | Improve Wi-Fi (closer to the router, 5 GHz), or raise the limit |
| "Last rejected reading: sent for an older target configuration" | Shown briefly after changing the target | Normal; it clears with the next reading |
| Red banner: connection to P1 lost | P1 stopped, went to sleep, or the network dropped | Restart P1; set P1's power plan so it does not sleep while plugged in |

**The position jumps around**: calibrate each anchor, keep people out of the
line between T and the anchors, raise *Kalman measurement noise*, and check
that the coordinates and heights in Settings match reality.

## Devices tab

| Problem | Fix |
|---|---|
| "No laptop anchor is connected" | The list comes from laptop anchors only (P1's own scanner counts). Start P1 without `--no-scan`, or start a laptop anchor. |
| A device I can see nearby is missing | It must be heard a few times before it is listed. Many devices advertise only while pairing or when their case or app is open. |
| The same device appears several times | Phones and wearables change their Bluetooth address every few minutes, so each address is a new row. Sort by signal removes the ones that have gone quiet. |
| The list keeps growing | Rows stay until you press Sort by signal or they've been silent for 5 minutes; that's intentional, so the list doesn't jump. Use Hide unnamed devices and the filter. |
| After Track, the Live tab says "Target not seen" | Tracking by name needs a device that advertises its name in most packets. Tracking by address stops when the device changes its address; pick it again, or use a device with a fixed name or custom UUID (like T in nRF Connect). |
| Two rows are marked Tracking | Both advertise exactly the tracked name. Give T a unique name, or a custom UUID in nRF Connect. |

## Phones as anchors

| Message on the Connect tab | Fix |
|---|---|
| iPhone and iPad browsers can't scan | iOS browsers have no Web Bluetooth scanning. Use a laptop. |
| Chrome only allows Bluetooth on secure pages | In `chrome://flags`, enable *Insecure origins treated as secure* with the exact address shown (e.g. `http://192.168.1.10:8780`), and enable *Experimental Web Platform features*. Relaunch Chrome. |
| This browser can't listen to Bluetooth advertisements yet | Enable *Experimental Web Platform features* in `chrome://flags` and relaunch. On desktop browsers scanning isn't available: use the laptop app. |
| Bluetooth scanning was not allowed | Tap Start scanning again and choose Allow. Check Chrome's site settings for this address and Android's Location and Nearby devices permissions for Chrome. |
| Paused: Chrome stops Bluetooth scanning... | The page went to the background or the screen turned off. Bring it back and tap *Resume scanning*. |

If P1's address changes, the insecure-origin flag must be updated with the new address.

## Packaged app

| Problem | Fix |
|---|---|
| "Windows protected your PC" (SmartScreen) | The exe is not code-signed. Choose *More info* > *Run anyway*. |
| Antivirus deletes or blocks BLELocate.exe or a `.pyd` | Restore it and add an exclusion for the BLE Locate folder. |
| "BLELocate.exe is not next to this file" | Unzip the whole folder; the launchers, `BLELocate.exe` and `_internal` must stay together. |
| Packaged self-test fails, but `.venv` works | Rebuild with `build_exe.bat`. It stops with the exact missing file if the bundle is incomplete. |
| "Port 8780 is already in use" | BLE Locate is already running (check the taskbar), or another program uses the port. Close it, or start with `--port 8790` and run the firewall script with `-HttpPort 8790`. |
| Built on x64 but the laptop is ARM64 (or the reverse) | Build on a laptop of the same architecture. |
