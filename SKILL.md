---
name: airfieldphase
description: "KFAA T-21 flying phase determination from METAR/TAF. Use when a pilot asks for the current flying phase, weather phase, airfield phase, or types /airfieldphase. Parses METAR observations for OEKF (King Faisal Air Academy), determines the phase per LOP Table 5-4, selects alternate airfields, calculates divert fuel, and applies Bird-Strike Risk Level restrictions per LOP 5-13."
---

# FlyingPhase — KFAA T-21 Flying Phase Skill

Determine the current flying phase at King Faisal Air Academy (OEKF) from a METAR string, per LOP Table 5-4.

## Trigger

`/airfieldphase` followed by structured input. Also triggers on natural language like "what's the phase", "check the weather phase", or "airfield phase".

## Input Format

```
/airfieldphase METAR: <metar> [TAF: <taf>] [WARNINGS: <text>] [BIRDS: low|moderate|severe] [NOTES: <notes>]
```

All fields except METAR are optional. Examples:

```
/airfieldphase METAR: OEKF 311200Z 33012KT 3000 BKN012 18/12 Q1012
```

```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 BECMG 3106/3108 15010KT WARNINGS: CB reported 25NM southwest BIRDS: moderate NOTES: RADAR procedures only, No medical
```

If the user omits labels and pastes a raw METAR string, treat the entire input as the METAR.

## Parsing User Input

Extract these fields:

1. **METAR** (required) — OEKF METAR string after `METAR:` (or the entire input if no labels)
2. **TAF** (optional) — OEKF TAF string after `TAF:`
3. **WARNINGS** (optional) — weather warnings after `WARNINGS:`
4. **BIRDS** (optional) — Bird-Strike Risk Level after `BIRDS:` — one of `low`, `moderate`, `severe`. Default: `low`
5. **NOTES** (optional) — operational notes after `NOTES:` (comma-separated)

## Running the Script

```bash
python3 scripts/flyingphase.py "<METAR>" ["<TAF>"] [--warning "<text>"] [--bird low|moderate|severe] [--notes "note1" "note2"] [--rwy 33L] [--solo] [--checks] [--json]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| METAR string | Yes | First positional arg — the OEKF METAR |
| TAF string | No | Second positional arg — the OEKF TAF |
| `--warning` | No | Weather warning text |
| `--bird` | No | Bird-Strike Risk Level: `low` (default), `moderate`, `severe` |
| `--notes` | No | Operational notes (space-separated strings) |
| `--rwy 33L` | No | Runway override (auto-selects from wind if omitted) |
| `--solo` | No | Solo cadet fuel adjustment (+100 lbs) |
| `--opposite` | No | Opposite-side divert fuel (+30 lbs) |
| `--checks` | No | Show pass/fail for each phase condition |
| `--json` | No | JSON output |
| `--no-cache` | No | Bypass TAF cache (re-fetch from API) |
| `--sortie-time` | No | Sortie time HHmm (e.g. 1030) — shows conditions for ±1hr window |

### Example Command Construction

User sends:
```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 WARNINGS: CB 25NM SW BIRDS: moderate NOTES: RADAR only, No medical
```

Run:
```bash
python3 scripts/flyingphase.py "METAR OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012" "TAF OEKF 302200Z 3100/3124 28015KT 6000 SCT050" --warning "CB 25NM SW" --bird moderate --notes "RADAR only" "No medical"
```

Note: Prefix the METAR string with `METAR ` and TAF string with `TAF ` if the user didn't include those prefixes.

## Output

Return the script output directly — do not modify or summarise it.

## Phases (LOP Table 5-4)

| Phase | Vis | Cloud | Wind | Solo |
|-------|-----|-------|------|------|
| 🟢 UNRESTRICTED | ≥8km | None <8000ft, max FEW above | ≤25kt total, ≤15kt xwind | ✅ |
| 🟡 RESTRICTED | ≥8km | None <6000ft, max SCT above | ≤25kt total, ≤15kt xwind | ✅ Post-IIC |
| 🟡 FS VFR | ≥5km | None <5000ft | ≤25kt total, ≤15kt xwind | ✅ 1st Solo |
| 🟠 VFR | ≥5km | ≥1500ft ceiling | ≤30kt total, ≤24kt xwind | ❌ |
| 🔴 IFR | Above IAP mins | Ceiling ≥ minima+300ft | ≤30kt total, ≤24kt xwind | ❌ |
| ⛔ HOLD | Below IFR limits | Below IFR limits | Exceeds limits | Recovery only |
| 🚨 RECALL | Rapid deterioration | CB within 30NM | >35kt | RTB |

## Bird-Strike Risk Levels (LOP 5-13)

Bird activity above LOW negates all solo phases. Highest declarable phase with birds > LOW is **VFR**.

| Level | Phase Cap | Restrictions |
|-------|-----------|-------------|
| LOW (default) | None | None |
| MODERATE | VFR max | No formation wing T/O, no solo cadet T/O |
| SEVERE | VFR max | No take-offs. Single aircraft straight-in recovery only |

## Alternates (Priority Order)

OEGS → OESD → OERK → OEDM → OEPS → OEHL → OEAH → OEDR

Live TAF auto-fetched for alternate weather assessment.

## Dependencies

Python 3.7+ stdlib only. Internet required for live TAF fetch (optional).

## Telegram Custom Command

Add to `channels.telegram.customCommands`:
```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```
