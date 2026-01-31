# FlyingPhase — KFAA T-21 Flying Phase Determination

A [Clawdbot](https://github.com/clawdbot/clawdbot) skill for **King Faisal Air Academy (OEKF)** instructor pilots flying the T-21 Hawk.

Paste a METAR → get the flying phase, alternate recommendation, and divert fuel — all per LOP Table 5-4.

## What It Does

- **Phase determination** — UNRESTRICTED through RECALL, based on visibility, cloud, wind, temperature
- **Automatic runway selection** — picks best runway from wind, calculates crosswind/headwind/tailwind
- **Live alternate weather** — fetches TAFs from aviationweather.gov for 8 alternate airfields
- **Alternate suitability** — checks ceiling, visibility, crosswind limits, and IAP availability
- **Divert fuel calculation** — base fuel + solo cadet (+100 lbs) + headwind adjustments

## Install

```bash
# Clone into your Clawdbot skills folder
cd ~/clawd/skills   # or your workspace skills directory
git clone https://github.com/nathan-85/flyingphase
```

### Telegram Command (Optional)

To get `/airfieldphase` in your Telegram command menu, add this to your Clawdbot config (`~/.clawdbot/clawdbot.json`) under `channels.telegram.customCommands`:

```json
{ "command": "airfieldphase", "description": "KFAA flying phase from METAR" }
```

Then restart: `clawdbot gateway restart`

## Usage

In Telegram (or any Clawdbot chat):

```
/airfieldphase METAR OEKF 311200Z 33012KT 3000 BKN012 18/12 Q1012
```

Or just paste a METAR and ask "what's the phase?"

### Example Output

```
🔴 KFAA Phase: IFR

📊 Conditions (OEKF):
  Vis: 3.0km | Cloud: BKN012
  Wind: 330°/12kt
  RWY 33R: ⨯ 0.0kt | ↑ 12.0kt

👨‍✈️ Restrictions:
  Solo cadets: ❌
  1st Solo: ❌

✈️  Alternate: REQUIRED
  ✅ Selected: OEGS (Gassim)
  Fuel: 504 lbs (480 base + 24 headwind adj)
  Approach: ILS CAT I (mins: 214ft / 800m)
```

## Requirements

- **Clawdbot** — [install guide](https://docs.clawd.bot)
- **Python 3.7+** — stdlib only, no pip packages
- **Internet** — for live TAF fetching (works without, just skips alternate analysis)

## Airfield Data

Approach minimums for civil airports (OEGS, OERK) are confirmed from the Saudi GACA AIP. Military field minimums are conservative estimates. See `TODO.md` for data status.

If you have access to official approach charts, you can update `scripts/airfield_data.json` directly.

## Disclaimer

This tool is for **planning reference only**. Always use official weather sources and follow your unit's procedures for actual phase determination. Approach minimums for some airfields are estimates — verify against published charts.

## License

MIT
