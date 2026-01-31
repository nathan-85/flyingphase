---
name: flyingphase
description: "KFAA T-21 flying phase determination from METAR/TAF. Use when a pilot asks for the current flying phase, weather phase, airfield phase, or types /airfieldphase. Parses METAR observations for OEKF (King Faisal Air Academy), determines the phase per LOP Table 5-4, selects alternate airfields, and calculates divert fuel."
---

# FlyingPhase — KFAA T-21 Flying Phase Skill

Determines the current flying phase at King Faisal Air Academy (OEKF) from a METAR string, per LOP Table 5-4.

## Trigger

`/airfieldphase` followed by a METAR string. Also triggers on natural language like "what's the phase" or "check the weather phase".

## Usage

Run the script with a METAR string:

```bash
python3 scripts/flyingphase.py "METAR OEKF 311200Z 33012KT 3000 BKN012 18/12 Q1012"
```

### Options

| Flag | Purpose |
|------|---------|
| `--rwy 33L` | Specify runway (auto-selects from wind if omitted) |
| `--solo` | Solo cadet fuel adjustment (+100 lbs) |
| `--opposite` | Opposite-side divert adjustment (+30 lbs) |
| `--warning "CB 25NM SW"` | Add weather warning text |
| `--checks` | Show ✅/❌ for each phase condition |
| `--json` | JSON output for programmatic use |

### With TAF (for divert planning)

```bash
python3 scripts/flyingphase.py \
  "METAR OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012" \
  "TAF OEKF 302200Z 3100/3124 28015KT 6000 SCT050 BECMG 3106/3108 15010KT 9999 FEW050" \
  --solo --checks
```

## What It Does

1. **Parses METAR** — wind, visibility, clouds, temperature, QNH (handles CAVOK, NSC, variable winds, gusts, RVR)
2. **Determines phase** from LOP Table 5-4: UNRESTRICTED → RESTRICTED → FS VFR → VFR → IFR → HOLD → RECALL
3. **Auto-selects runway** based on wind (or manual `--rwy`)
4. **Calculates crosswind/headwind/tailwind** components (gusts = effective wind)
5. **Fetches live TAFs** from aviationweather.gov for alternate airfields
6. **Selects best alternate** — checks suitability (ceiling, vis, crosswind, approach availability)
7. **Calculates divert fuel** — base + solo + headwind adjustments

## Phases (LOP Table 5-4)

| Phase | Vis | Cloud | Wind | Solo |
|-------|-----|-------|------|------|
| 🟢 UNRESTRICTED | ≥8km | None <8000ft, max FEW above | ≤25kt, ≤15kt xwind | ✅ T-21 |
| 🟡 RESTRICTED | ≥8km | None <6000ft, max SCT above | ≤25kt, ≤15kt xwind | ✅ Post-IIC |
| 🟡 FS VFR | ≥5km | None <5000ft | ≤25kt, ≤15kt xwind | ✅ 1st Solo |
| 🟠 VFR | ≥5km | ≥1500ft ceiling | ≤30kt, ≤24kt xwind | ❌ |
| 🔴 IFR | Above IAP mins | Ceiling ≥ minima+300ft | ≤30kt, ≤24kt xwind | ❌ |
| ⛔ HOLD | Below IFR | Below IFR | Exceeds limits | ❌ Recover only |
| 🚨 RECALL | Rapid deterioration | CB within 30NM | >35kt | ❌ RTB |

## Alternates (Priority Order)

| # | ICAO | Name | Fuel | Distance |
|---|------|------|------|----------|
| 1 | OEGS | Gassim | 480 lbs | 78 NM |
| 2 | OESD | King Saud AB | 530 lbs | 110 NM |
| 3 | OERK | King Khalid Intl | 530 lbs | 107 NM |
| 4 | OEDM | Dawadmi | 540 lbs | 114 NM |
| 5 | OEPS | Prince Sultan AB | 610 lbs | 177 NM |
| 6 | OEHL | Hail | 660 lbs | 205 NM |
| 7 | OEAH | Al-Ahsa | 690 lbs | 238 NM |
| 8 | OEDR | Dhahran | 700 lbs | 266 NM |

## Airfield Data

All runway headings, approach types, and minimums are in `scripts/airfield_data.json`. Approach minimums sourced from Saudi GACA AIP where available (OEGS, OERK confirmed). Military field minimums are conservative estimates — see TODO.md for status.

## Dependencies

Python 3.7+ stdlib only. No pip packages needed. Internet connection for live TAF fetching (optional — works without, just skips alternate TAF analysis).

## Setup for Telegram

Add to your Clawdbot config under `channels.telegram.customCommands`:

```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```

Then restart the gateway. The `/airfieldphase` command will appear in Telegram's command menu.
