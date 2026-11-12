# Ultimate Printer Resource Monitor

A desktop window that shows toner levels, paper tray levels, and alerts for the printers on your network. It also totals how many sheets and reams are needed to refill every printer on screen.

It reads the standard Printer MIB over SNMP, so it works with most network printers. Pictures are bundled for several Ricoh multifunction models.

## Requirements

- Python 3.11 or newer
- A computer on the same network as the printers
- SNMP v1/v2c enabled on each printer (most have it on by default, with the community `public`)

## Install and run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python printer_monitor.py
```

On later runs, only the `Activate.ps1` and `python printer_monitor.py` lines are needed.

## Adding your printers

The program starts with an empty printer list.

1. Open `File -> Edit printers...`
2. Click `Add...`
3. Fill in the details and click `OK`
4. Click `Save`

| Field | Required | Notes |
|-|-|-|
| Name | Yes | Shown above the printer. Must be unique. |
| IP address or host name | Yes | For example `192.0.2.10` or `printer1.example.com` |
| SNMP community | Yes | Usually `public` |
| SNMP port | Yes | Usually `161` |
| Serial number | No | Shown when hovering over the name. Read from the printer if left blank. |
| Asset tag | No | Shown when hovering over the name |
| Web page address | No | Opened when the address is clicked. Defaults to `http://<address>/` |
| Show when the program starts | | Untick for printers you only check now and then |

Use `Move up` and `Move down` to set the order of the printers in the window.

## Using the window

- Tick or untick a printer on the left to show or hide it.
- `Sheets needed` and `Reams needed` cover the printers currently shown.
- `Reload every N seconds` controls automatic refresh. `Refresh now` or `F5` refreshes immediately.
- A tray turns red once it can take a full ream. Toner turns red at 20% or lower.
- Hover over a toner bar to see its name. Hover over the paper bar to see the sheet count.

## Where settings are stored

Settings live in `config.json` in a per-user folder. `File -> Open data folder` opens it.

| System | Folder |
|-|-|
| Windows | `%APPDATA%\UltimatePrinterResourceMonitor` |
| macOS | `~/Library/Application Support/UltimatePrinterResourceMonitor` |
| Linux | `~/.config/UltimatePrinterResourceMonitor` |

Set the `UPRM_DATA_DIR` environment variable to use a different folder.

These settings have no screen in the program yet. Close the program, edit `config.json`, and start it again.

| Setting | Default | Meaning |
|-|-|-|
| `ream_size` | 500 | Sheets per ream |
| `low_supply_percent` | 20 | Toner level shown in red |
| `snmp_timeout` | 2 | Seconds to wait for a printer to answer |
| `snmp_retries` | 2 | Attempts before a printer is marked as not connected |

The log file is `logs/monitor.log` in the same folder. `File -> Open log file` opens it.

### Custom printer pictures

Printers without a bundled picture show a placeholder. To add your own, save a PNG named after the model exactly as the program displays it, for example `IM C3000.png`, in the `images` folder inside the data folder.

## How it works

Each printer is queried on a background thread, so a slow or unreachable printer does not freeze the window.

| Value | OID | Method |
|-|-|-|
| Model | 1.3.6.1.2.1.43.5.1.1.16.1 | Get |
| Serial number | 1.3.6.1.2.1.43.5.1.1.17.1 | Get |
| Supply names | 1.3.6.1.2.1.43.11.1.1.6.1 | Walk |
| Supply types | 1.3.6.1.2.1.43.11.1.1.5.1 | Walk |
| Supply capacity | 1.3.6.1.2.1.43.11.1.1.8.1 | Walk |
| Supply levels | 1.3.6.1.2.1.43.11.1.1.9.1 | Walk |
| Tray names | 1.3.6.1.2.1.43.8.2.1.13.1 | Walk |
| Tray capacity | 1.3.6.1.2.1.43.8.2.1.9.1 | Walk |
| Tray levels | 1.3.6.1.2.1.43.8.2.1.10.1 | Walk |
| Alerts | 1.3.6.1.2.1.43.18.1.1.8.1 | Walk |

If a printer has no model value, the program falls back to its device description (`1.3.6.1.2.1.25.3.2.1.3.1`) and then its system description (`1.3.6.1.2.1.1.1.0`).

Waste toner containers and bypass trays are left out.

## Known issues

- Printers may not report paper level changes until they wake from energy saver mode.
- Some trays are split into left and right compartments, but the printer reports one combined level.
- Some printers report only "some remaining" instead of a number. These show as `OK` for toner or `?` for trays, and are left out of the paper totals.
- Only SNMP v1/v2c is supported. SNMP v3 is not.

## Building a standalone executable

```powershell
pip install pyinstaller
pyinstaller printer_monitor.spec
```

The result is `dist\Ultimate Printer Resource Monitor.exe`.

## Development

```powershell
pip install -e ".[dev]"
pytest
ruff check .
```

| Path | Purpose |
|-|-|
| `resource_monitor/snmp.py` | Reads and interprets printer values. No user interface code. |
| `resource_monitor/config.py` | Loads, validates, and saves settings |
| `resource_monitor/models.py` | Data objects for supplies, trays, and printer status |
| `resource_monitor/gui.py` | The Tkinter window and dialogs |
| `resource_monitor/assets/` | Bundled pictures and icons |
| `tests/` | Tests, including a simulated printer that needs no network |

## License and credits

MIT License. See [LICENSE](LICENSE). Originally created by Emilio Garcia.
