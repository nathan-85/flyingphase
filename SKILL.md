---
name: flyingphase
description: "KFAA T-21 flying phase determination from METAR/TAF. Use when a pilot asks for the current flying phase, weather phase, airfield phase, or types /airfieldphase. Parses METAR observations for OEKF (King Faisal Air Academy), determines the phase per LOP Table 5-4, selects alternate airfields, and calculates divert fuel."
---

# FlyingPhase — KFAA T-21 Flying Phase Skill

Determines the current flying phase at King Faisal Air Academy (OEKF) from a METAR string, per LOP Table 5-4.

## Trigger

`/airfieldphase` followed by structured input. Also triggers on natural language like "what's the phase" or "check the weather phase".

## Input Format

Users provide data in this format:

```
/airfieldphase METAR: <metar string> TAF: <taf string> WARNINGS: <warning text> NOTES: <notes>
```

All fields except METAR are optional. Examples:

```
/airfieldphase METAR: OEKF 311200Z 33012KT 3000 BKN012 18/12 Q1012
```

```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 BECMG 3106/3108 15010KT WARNINGS: CB reported 25NM southwest NOTES: RADAR procedures only, No medical
```

## Parsing the User Input

Extract these fields from the user's message:

1. **METAR** (required) — the OEKF METAR string after `METAR:`
2. **TAF** (optional) — the OEKF TAF string after `TAF:`
3. **WARNINGS** (optional) — weather warning text after `WARNINGS:`
4. **NOTES** (optional) — operational notes after `NOTES:` (comma-separated items like "RADAR procedures only", "No medical")

If the user omits labels and just pastes a raw METAR string, treat the entire input as the METAR.

## Running the Script

```bash
python3 scripts/flyingphase.py "<METAR>" ["<TAF>"] [--warning "<warning text>"] [--notes "note1" "note2"] [--rwy 33L] [--solo] [--checks]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| METAR string | Yes | First positional arg — the OEKF METAR |
| TAF string | No | Second positional arg — the OEKF TAF |
| `--warning` | No | Weather warning text |
| `--notes` | No | Operational notes (multiple strings) |
| `--rwy 33L` | No | Runway override (auto-selects from wind if omitted) |
| `--solo` | No | Solo cadet fuel adjustment (+100 lbs) |
| `--opposite` | No | Opposite-side divert fuel (+30 lbs) |
| `--checks` | No | Show ✅/❌ for each phase condition |
| `--json` | No | JSON output |

### Example Command Construction

User sends:
```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 WARNINGS: CB 25NM SW NOTES: RADAR only, No medical
```

Run:
```bash
python3 scripts/flyingphase.py "METAR OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012" "TAF OEKF 302200Z 3100/3124 28015KT 6000 SCT050" --warning "CB 25NM SW" --notes "RADAR only" "No medical"
```

Note: Prefix the METAR string with "METAR " and TAF string with "TAF " if the user didn't include those prefixes.

## Output

The script outputs a formatted phase report. Return it directly to the user — do not modify or summarise it.

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

OEGS (480 lbs/78NM), OESD (530/110), OERK (530/107), OEDM (540/114), OEPS (610/177), OEHL (660/205), OEAH (690/238), OEDR (700/266)

## Dependencies

Python 3.7+ stdlib only. Internet for live TAF fetch (optional).

## Telegram Setup

Add to `channels.telegram.customCommands`:
```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```
