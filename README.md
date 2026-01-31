# Airfield Phase — KFAA T-21 Flying Phase Tool

Determines the current flying phase at King Faisal Air Academy (OEKF) from a METAR observation, per LOP Table 5-4.

Built for [Clawdbot](https://docs.clawd.bot) — your bot reads `SKILL.md` and handles the rest.

## What It Does

- Parses METAR (and optionally TAF, PIREP, warnings) to determine the flying phase
- Selects alternate airfields with live TAF weather assessment
- Calculates divert fuel (T-21 specific)
- Fetches NOTAMs for OEKF and all alternates (FAA API)
- Applies Bird-Strike Risk Level restrictions (LOP 5-13)

### Phases

| Phase | Meaning |
|-------|---------|
| 🟢 UNRESTRICTED | Full ops, solo approved |
| 🟡 RESTRICTED | Limited cloud/vis, post-IIC solo only |
| 🟡 FS VFR | First solo eligible |
| 🟠 VFR | No solo |
| 🔴 IFR | Instrument approaches only |
| ⛔ HOLD | Below IFR — recovery only |
| 🚨 RECALL | RTB — CB, severe wind, rapid deterioration |

## Quick Start

### Clawdbot (automatic)

Drop this folder into your `skills/` directory. Your bot picks up `SKILL.md` and registers the `/airfieldphase` command.

```
/airfieldphase 05014KT 9999 NSC 20/03 Q1023
```

### Standalone

```bash
python3 scripts/flyingphase.py "05014KT 9999 NSC 20/03 Q1023"
```

With TAF and options:
```bash
python3 scripts/flyingphase.py \
  "05014KT 9999 NSC 20/03 Q1023" \
  "TAF 07010KT BKN040 9999 BECMG 0900/1100 5000 BLDU" \
  --bird moderate --verbose
```

## Requirements

- Python 3.7+
- No external dependencies (stdlib only)
- Internet for live TAF fetch and NOTAMs (optional — use `--no-notams` to skip)

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Agent instructions (Clawdbot reads this) |
| `scripts/flyingphase.py` | Main phase determination script |
| `scripts/weather_elements.py` | Weather element pipeline (multi-source conflict resolution) |
| `scripts/notam_checker.py` | FAA NOTAM fetcher and classifier |
| `scripts/airfield_data.json` | Airfield database (runways, approaches, minimums) |
| `scripts/test_flyingphase.py` | Test suite (122 tests) |
| `scripts/test_weather_elements.py` | Weather element tests (36 tests) |
| `references/` | Source documents (gitignored — not required to run) |

## Key Options

| Flag | Description |
|------|-------------|
| `--no-notams` | Skip NOTAM fetch (faster, offline-friendly) |
| `--verbose` | Show full weather element pipeline and alternate assessment details |
| `--bird low\|moderate\|severe` | Bird-Strike Risk Level |
| `--solo` | Solo cadet fuel adjustment (+100 lbs) |
| `--sortie-time HHmm` | Show conditions for a specific sortie window |
| `--rwy 33L` | Override runway selection |

## Telegram Setup

Add to your Clawdbot config under `channels.telegram.customCommands`:

```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```

## Notes

- Airfield data for military fields (OEKF, OESD, OEPS, OEDR) uses estimated minimums where official charts weren't available. See `TODO.md` for details.
- NOTAMs are fetched by default. The FAA API credentials are embedded — no setup needed.
- METAR parser is order-independent and handles abbreviated input (no station ID or timestamp required).
