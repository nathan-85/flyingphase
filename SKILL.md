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
/airfieldphase METAR: <metar> [TAF: <taf>] [PIREP: <pirep>] [WARNINGS: <text>] [BIRDS: low|moderate|severe] [NOTES: <notes>]
```

All fields except METAR are optional. Examples:

```
/airfieldphase METAR: OEKF 311200Z 33012KT 3000 BKN012 18/12 Q1012
```

```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 BECMG 3106/3108 15010KT PIREP: UA /OV OEKF /FL050 /SK BKN040CB /WX TS WARNINGS: CB reported 25NM southwest BIRDS: moderate NOTES: RADAR procedures only, No medical
```

If the user omits labels and pastes a raw METAR string, treat the entire input as the METAR.

## Parsing User Input

Extract these fields:

1. **METAR** (required) — OEKF METAR string after `METAR:` (or the entire input if no labels)
2. **TAF** (optional) — OEKF TAF string after `TAF:`
3. **PIREP** (optional) — Pilot report after `PIREP:` (e.g. `UA /OV OEKF /FL050 /SK BKN040CB /WX TS`)
4. **WARNINGS** (optional) — weather warnings after `WARNINGS:`
5. **BIRDS** (optional) — Bird-Strike Risk Level after `BIRDS:` — one of `low`, `moderate`, `severe`. Default: `low`
6. **NOTES** (optional) — operational notes after `NOTES:` (comma-separated)

## Running the Script

```bash
python3 scripts/flyingphase.py "<METAR>" ["<TAF>"] ["<PIREP>"] [--warning "<text>"] [--bird low|moderate|severe] [--notes "note1" "note2"] [--rwy 33L] [--solo] [--verbose] [--local-lookahead 60] [--notams] [--json]
```

Positional inputs (METAR, TAF, PIREP) are **auto-classified** — no labels needed. The script detects:
- **PIREP**: starts with `UA ` / `UUA ` or contains `/OV` + `/SK` fields
- **TAF**: starts with `TAF ` or contains `BECMG`/`FM` groups
- **METAR**: everything else

All positional args must come **before** any flags.

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| Positional inputs | Yes (1+) | METAR, TAF, PIREP strings — auto-detected |
| `--warning` | No | Weather warning text |
| `--bird` | No | Bird-Strike Risk Level: `low` (default), `moderate`, `severe` |
| `--notes` | No | Operational notes (space-separated strings) |
| `--rwy 33L` | No | Runway override (auto-selects from wind if omitted) |
| `--solo` | No | Solo cadet fuel adjustment (+100 lbs) |
| `--opposite` | No | Opposite-side divert fuel (+30 lbs) |
| `--verbose` | No | Show all weather inputs including Weather Element Pipeline |
| `--local-lookahead` | No | OEKF phase window in minutes (default: 60) |
| `--json` | No | JSON output |
| `--no-cache` | No | Bypass TAF cache (re-fetch from API) |
| `--sortie-time` | No | Sortie time HHmm (e.g. 1030) — shows conditions for ±1hr window |
| `--notams` | No | Fetch live NOTAMs for OEKF + alternates from FAA |

### METAR Parsing

The METAR parser is **order-independent** — elements can appear in any sequence. Station identifier (OEKF) and timestamp are optional; they default to OEKF and current UTC if omitted. This means pilots can paste abbreviated METARs like:

```
05018G22KT NSC 9999 20/03 NOSIG
```

### Phase Checks

Phase condition checks (✅/❌ per condition per phase) are **always shown** in output so users can verify the determination.

### Verbose Mode

When `--verbose` is passed, the output includes:

- **Phase Determination Inputs**: raw METAR observation values, TAF overlay changes (alternate assessment only)
- **Weather Element Pipeline**: all weather elements from every source (METAR, TAF, WARNING, PIREP) with validity windows, plus resolved worst-case conditions for phase window (METAR+WARNING+PIREP) and alternate window (all sources)
- **Alternate Assessment Inputs**: each alternate's TAF conditions (base/BECMG/TEMPO), runway/crosswind, approach type and minimums, rejection reasons

### Example Command Construction

User sends:
```
/airfieldphase METAR: OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012 TAF: OEKF 302200Z 3100/3124 28015KT 6000 SCT050 PIREP: UA /OV OEKF /FL050 /SK BKN040CB /WX TS WARNINGS: CB 25NM SW BIRDS: moderate NOTES: RADAR only, No medical
```

Run:
```bash
python3 scripts/flyingphase.py "METAR OEKF 311200Z 28018G25KT 5000 SCT040 32/18 Q1012" "TAF OEKF 302200Z 3100/3124 28015KT 6000 SCT050" "UA /OV OEKF /FL050 /SK BKN040CB /WX TS" --warning "CB 25NM SW" --bird moderate --notes "RADAR only" "No medical" --notams --verbose
```

Note: Prefix the METAR string with `METAR ` and TAF string with `TAF ` if the user didn't include those prefixes. PIREP strings are auto-detected (start with `UA` or `UUA`). Station identifier and timestamp are optional — the parser handles bare METAR elements.

User sends (simple):
```
/airfieldphase METAR: 33012KT 9999 FEW080 22/10 Q1018
```

Run:
```bash
python3 scripts/flyingphase.py "33012KT 9999 FEW080 22/10 Q1018" --notams --verbose
```

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

## NOTAM Integration

When `--notams` is passed, the tool fetches live NOTAMs from the FAA NOTAM Search API (no API key required) for OEKF and all alternate airfields:

- **Closed runways/aerodromes** disqualify alternates from selection
- **ILS/VOR/DME outages** shown as warnings per alternate
- **Bird activity NOTAMs** flagged
- Full NOTAM report appended to output with per-airfield breakdown

The NOTAM checker can also run standalone:
```bash
python3 scripts/notam_checker.py OEJD OERK OEGS [--json] [--timeout 15]
```

**Always pass `--notams` in the command** when the user runs `/airfieldphase`. This ensures alternate recommendations account for real-world NOTAM restrictions.

## Dependencies

Python 3.7+ stdlib only. Internet required for live TAF and NOTAM fetch (optional).

## Telegram Custom Command

Add to `channels.telegram.customCommands`:
```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```
