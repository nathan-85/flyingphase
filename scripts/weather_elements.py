#!/usr/bin/env python3
"""
Modular weather element system for KFAA Flying Phase Determination.

Each weather observation/forecast is decomposed into individual WeatherElements
with type, value, source, and validity period. Elements are collected into a
WeatherCollection and resolved through time-window filtering and worst-case
conflict resolution.

Element types: cloud, visibility, wind, weather
Sources: METAR, TAF, WARNING, PIREP
Validity: valid_from/valid_to (None = extends to ±∞)

Resolution rules:
  - visibility: lowest meters wins
  - wind: highest crosswind wins (runway-dependent)
  - cloud: merge layers, same-base conflicts → worst coverage (OVC/BKN > SCT > FEW)
  - weather/CB: union (present = present)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Set, Dict, Tuple
import math
import re


# Coverage ranking for cloud conflict resolution (OVC ≈ BKN)
COVERAGE_RANK = {'FEW': 1, 'SCT': 2, 'BKN': 3, 'OVC': 3}

# Phase and alternate source scopes
PHASE_SOURCES = {'METAR', 'WARNING', 'PIREP'}
ALTERNATE_SOURCES = {'METAR', 'TAF', 'WARNING', 'PIREP'}

# Default lookahead windows (minutes)
DEFAULT_LOCAL_LOOKAHEAD = 60
DEFAULT_ALTERNATE_LOOKAHEAD = 180


@dataclass
class WeatherElement:
    """A single weather data point with provenance and validity.
    
    Attributes:
        type: Element type — 'cloud', 'visibility', 'wind', 'weather'
        value: Type-specific value dict:
            - visibility: {'meters': int}
            - wind: {'direction': int|None, 'speed': int, 'gust': int|None}
            - cloud: {'coverage': str, 'height_ft': int, 'cb': bool}
            - weather: {'code': str}
        source: Data source — 'METAR', 'TAF', 'WARNING', 'PIREP'
        valid_from: Start of validity (None = -∞)
        valid_to: End of validity (None = +∞)
        raw: Original text this was parsed from
    """
    type: str
    value: dict
    source: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    raw: str = ''

    def overlaps_window(self, start: Optional[datetime], end: Optional[datetime]) -> bool:
        """Check if this element's validity overlaps with [start, end]."""
        if end is not None and self.valid_from is not None and self.valid_from > end:
            return False
        if start is not None and self.valid_to is not None and self.valid_to < start:
            return False
        return True


class WeatherCollection:
    """Collection of WeatherElements with filtering and worst-case resolution."""

    def __init__(self, elements: List[WeatherElement] = None):
        self.elements = list(elements) if elements else []

    def add(self, element: WeatherElement):
        self.elements.append(element)

    def add_all(self, elements: List[WeatherElement]):
        self.elements.extend(elements)

    def filter(self, window_start: Optional[datetime] = None,
               window_end: Optional[datetime] = None,
               sources: Optional[Set[str]] = None) -> 'WeatherCollection':
        """Return a new collection filtered by time window and/or source."""
        filtered = []
        for el in self.elements:
            if not el.overlaps_window(window_start, window_end):
                continue
            if sources is not None and el.source not in sources:
                continue
            filtered.append(el)
        return WeatherCollection(filtered)

    def resolve(self, runway_heading: Optional[int] = None) -> dict:
        """Resolve to worst-case conditions across all elements.

        Returns dict with:
            visibility_m: int or None
            clouds: list of {coverage, height_ft, cb} sorted by height
            wind: {direction, speed, gust} or None
            weather: set of codes
            has_cb: bool
        """
        result = {
            'visibility_m': None,
            'clouds': [],
            'wind': None,
            'weather': set(),
            'has_cb': False,
        }

        # --- Visibility: lowest meters ---
        vis_elements = [el for el in self.elements if el.type == 'visibility']
        if vis_elements:
            result['visibility_m'] = min(el.value['meters'] for el in vis_elements)

        # --- Wind: highest crosswind (runway-dependent) ---
        wind_elements = [el for el in self.elements if el.type == 'wind']
        if wind_elements:
            if runway_heading is not None:
                worst_wind = None
                worst_xwind = -1
                for el in wind_elements:
                    eff = el.value.get('gust') or el.value.get('speed', 0)
                    d = el.value.get('direction')
                    if d is not None:
                        angle = abs(d - runway_heading)
                        if angle > 180:
                            angle = 360 - angle
                        xwind = abs(eff * math.sin(math.radians(angle)))
                    else:
                        xwind = eff  # VRB = assume full crosswind
                    if xwind > worst_xwind:
                        worst_xwind = xwind
                        worst_wind = dict(el.value)
                result['wind'] = worst_wind
            else:
                worst = max(wind_elements,
                            key=lambda el: el.value.get('gust') or el.value.get('speed', 0))
                result['wind'] = dict(worst.value)

        # --- Cloud: merge layers, same-base → worst coverage ---
        cloud_elements = [el for el in self.elements if el.type == 'cloud']
        by_height: Dict[int, dict] = {}

        for el in cloud_elements:
            h = el.value['height_ft']
            cov = el.value['coverage']
            rank = COVERAGE_RANK.get(cov, 0)

            if h not in by_height or rank > COVERAGE_RANK.get(by_height[h]['coverage'], 0):
                by_height[h] = dict(el.value)

        result['clouds'] = sorted(by_height.values(), key=lambda c: c['height_ft'])

        # CB from cloud layers
        for c in result['clouds']:
            if c.get('cb'):
                result['has_cb'] = True
                break

        # --- Weather: union of all codes ---
        weather_elements = [el for el in self.elements if el.type == 'weather']
        for el in weather_elements:
            code = el.value.get('code', '')
            result['weather'].add(code)
            if code in ('CB', 'TS') or code.startswith('TS'):
                result['has_cb'] = True

        return result

    def describe(self) -> List[str]:
        """Human-readable description of all elements (for verbose output)."""
        lines = []
        for source in ['METAR', 'PIREP', 'TAF', 'WARNING']:
            source_els = [el for el in self.elements if el.source == source]
            if not source_els:
                continue
            lines.append(f"  {source}:")
            for el in source_els:
                vf = el.valid_from.strftime('%d%H%MZ') if el.valid_from else '−∞'
                vt = el.valid_to.strftime('%d%H%MZ') if el.valid_to else '+∞'
                lines.append(f"    [{vf} → {vt}] {el.type}: {format_element_value(el)}  ({el.raw})")
        return lines

    def __len__(self):
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"WeatherCollection({len(self.elements)} elements)"


# ============================================================
# Display helpers
# ============================================================

def format_element_value(el: WeatherElement) -> str:
    """Format an element's value for human display."""
    v = el.value
    if el.type == 'visibility':
        m = v['meters']
        return f"{m}m ({m/1000:.1f}km)" if m < 10000 else "10km+"
    elif el.type == 'wind':
        d = f"{v['direction']:03d}°" if v.get('direction') is not None else "VRB"
        s = f"{v['speed']}kt"
        g = f" G{v['gust']}" if v.get('gust') else ""
        return f"{d}/{s}{g}"
    elif el.type == 'cloud':
        s = f"{v['coverage']}{v['height_ft']//100:03d}"
        if v.get('cb'):
            s += 'CB'
        return s
    elif el.type == 'weather':
        return v.get('code', '?')
    return str(v)


# ============================================================
# PARSERS: Emit WeatherElements from various sources
# ============================================================

def parse_metar_elements(metar, obs_time: Optional[datetime] = None) -> List[WeatherElement]:
    """Convert a parsed METARParser into WeatherElements.

    Args:
        metar: METARParser instance (already parsed)
        obs_time: Override observation time (defaults to METAR timestamp or now)

    Returns:
        List of WeatherElements with source='METAR', valid_from=obs_time, valid_to=None
    """
    elements = []

    # Determine observation time
    if obs_time is None:
        now = datetime.now(timezone.utc)
        if metar.obs_hour is not None:
            try:
                day = metar.obs_day if metar.obs_day is not None else now.day
                obs_time = now.replace(day=day, hour=metar.obs_hour,
                                       minute=metar.obs_minute or 0,
                                       second=0, microsecond=0)
            except ValueError:
                obs_time = now
        else:
            obs_time = now

    # Visibility
    if metar.visibility_m is not None:
        elements.append(WeatherElement(
            type='visibility',
            value={'meters': metar.visibility_m},
            source='METAR',
            valid_from=obs_time, valid_to=None,
            raw=f"{metar.visibility_m}" if metar.visibility_m < 10000 else "9999"
        ))
    elif metar.cavok:
        elements.append(WeatherElement(
            type='visibility',
            value={'meters': 10000},
            source='METAR',
            valid_from=obs_time, valid_to=None,
            raw='CAVOK'
        ))

    # Wind
    if metar.wind_speed is not None:
        d = metar.wind_dir
        raw_d = f"{d:03d}" if d is not None else "VRB"
        raw_g = f"G{metar.wind_gust}" if metar.wind_gust else ""
        elements.append(WeatherElement(
            type='wind',
            value={'direction': d, 'speed': metar.wind_speed, 'gust': metar.wind_gust},
            source='METAR',
            valid_from=obs_time, valid_to=None,
            raw=f"{raw_d}{metar.wind_speed:02d}{raw_g}KT"
        ))

    # Clouds
    for cloud in metar.clouds:
        cb = cloud.get('type') == 'CB'
        elements.append(WeatherElement(
            type='cloud',
            value={'coverage': cloud['coverage'], 'height_ft': cloud['height_ft'], 'cb': cb},
            source='METAR',
            valid_from=obs_time, valid_to=None,
            raw=f"{cloud['coverage']}{cloud['height_ft']//100:03d}{'CB' if cb else ''}"
        ))

    # Weather phenomena
    for wx in metar.weather:
        elements.append(WeatherElement(
            type='weather',
            value={'code': wx.upper()},
            source='METAR',
            valid_from=obs_time, valid_to=None,
            raw=wx
        ))

    # Ensure TS is captured as CB indicator
    if metar.has_ts_weather:
        codes = {el.value['code'] for el in elements if el.type == 'weather'}
        if 'CB' not in codes and 'TS' not in codes:
            elements.append(WeatherElement(
                type='weather', value={'code': 'TS'}, source='METAR',
                valid_from=obs_time, valid_to=None, raw='TS'
            ))

    return elements


def _build_datetime(ref: datetime, day: Optional[int], hour: int, minute: int = 0) -> datetime:
    """Build a datetime from day/hour relative to a reference date."""
    d = day if day is not None else ref.day
    try:
        dt = ref.replace(day=d, hour=hour % 24, minute=minute, second=0, microsecond=0)
        # Handle day wrap: if result is far behind ref, it's probably next month
        if dt < ref - timedelta(days=15):
            # Crossed a month boundary forward
            if ref.month == 12:
                dt = dt.replace(year=ref.year + 1, month=1)
            else:
                dt = dt.replace(month=ref.month + 1)
        return dt
    except ValueError:
        return ref.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)


def parse_taf_elements(taf, ref_time: Optional[datetime] = None) -> List[WeatherElement]:
    """Convert a parsed TAFParser into WeatherElements with proper validity.

    Implements BECMG/FM/BASE end-time lookahead:
    - For each element type, look ahead through subsequent BECMG/FM groups
      (skip TEMPO) for the next group containing a matching type.
    - BECMG match: valid_to = that BECMG's second time
    - FM match: valid_to = that FM's time
    - No match: valid_to = None (+∞)
    - TEMPO elements: valid_from = first time, valid_to = second time (fixed)

    Args:
        taf: TAFParser instance (already parsed)
        ref_time: Reference time for building datetimes (defaults to now)
    """
    if ref_time is None:
        ref_time = datetime.now(timezone.utc)

    elements = []

    # Collect all change groups in sequential order
    groups = []

    if taf.base_period:
        groups.append({
            'kind': 'BASE',
            'period': taf.base_period,
            'from_utc': taf.base_period.get('valid_from_utc'),
            'to_utc': taf.base_period.get('valid_to_utc'),
        })

    # Interleave BECMG, TEMPO, FM in time order
    for p in taf.becmg_periods:
        groups.append({
            'kind': 'BECMG', 'period': p,
            'from_utc': p.get('valid_from_utc'),
            'to_utc': p.get('valid_to_utc'),
        })
    for p in taf.tempo_periods:
        groups.append({
            'kind': 'TEMPO', 'period': p,
            'from_utc': p.get('valid_from_utc'),
            'to_utc': p.get('valid_to_utc'),
        })
    for p in taf.fm_periods:
        groups.append({
            'kind': 'FM', 'period': p,
            'from_utc': p.get('valid_from_utc'),
            'to_utc': p.get('valid_to_utc'),
        })

    # Sort by start time (BASE first with sentinel -1)
    groups.sort(key=lambda g: g['from_utc'] if g['from_utc'] is not None else -1)

    # Process each group
    for gi, group in enumerate(groups):
        kind = group['kind']
        period = group['period']
        from_h = group['from_utc']
        to_h = group['to_utc']

        # Extract raw weather data from period
        period_els = _extract_period_elements(period)

        for el_type, el_value, el_raw in period_els:
            if kind == 'TEMPO':
                # TEMPO: fixed window, not permanent
                vf = _build_datetime(ref_time, None, from_h) if from_h is not None else None
                vt = _build_datetime(ref_time, None, to_h) if to_h is not None else None
            else:
                # BASE / BECMG / FM
                if kind == 'BASE':
                    vf = None  # extends to -∞
                else:
                    vf = _build_datetime(ref_time, None, from_h) if from_h is not None else None

                # Lookahead for end validity
                vt = _lookahead_end_time(groups, gi, el_type, ref_time)

            elements.append(WeatherElement(
                type=el_type, value=el_value, source='TAF',
                valid_from=vf, valid_to=vt, raw=el_raw
            ))

    return elements


def _extract_period_elements(period: dict) -> List[Tuple[str, dict, str]]:
    """Extract (type, value, raw) tuples from a TAF period dict."""
    elements = []

    vis = period.get('visibility_m')
    if vis is not None:
        raw = 'CAVOK' if vis == 10000 else str(vis)
        elements.append(('visibility', {'meters': vis}, raw))

    if period.get('wind_speed') is not None:
        d = period.get('wind_dir')
        raw_d = f"{d:03d}" if d is not None else "VRB"
        g = period.get('wind_gust')
        raw_g = f"G{g}" if g else ""
        elements.append(('wind', {
            'direction': d,
            'speed': period['wind_speed'],
            'gust': g,
        }, f"{raw_d}{period['wind_speed']:02d}{raw_g}KT"))

    for cloud in period.get('clouds', []):
        cb = cloud.get('type') == 'CB'
        elements.append(('cloud', {
            'coverage': cloud['coverage'],
            'height_ft': cloud['height_ft'],
            'cb': cb,
        }, f"{cloud['coverage']}{cloud['height_ft']//100:03d}{'CB' if cb else ''}"))

    if period.get('has_cb'):
        elements.append(('weather', {'code': 'CB'}, 'CB'))
    for wx in period.get('weather', []):
        if wx != 'CB':  # Avoid duplicate if has_cb already added
            elements.append(('weather', {'code': wx}, wx))

    return elements


def _lookahead_end_time(groups: list, current_idx: int, el_type: str,
                        ref_time: datetime) -> Optional[datetime]:
    """Look ahead for the next BECMG/FM group with a matching element type.

    Returns:
        - BECMG match: that BECMG's second time (new value established)
        - FM match: that FM's time (instant replacement)
        - No match: None (+∞)
    """
    for j in range(current_idx + 1, len(groups)):
        g = groups[j]
        if g['kind'] == 'TEMPO':
            continue  # TEMPO doesn't permanently supersede

        period = g['period']
        has_match = False

        if el_type == 'visibility' and period.get('visibility_m') is not None:
            has_match = True
        elif el_type == 'wind' and period.get('wind_speed') is not None:
            has_match = True
        elif el_type == 'cloud' and period.get('clouds'):
            has_match = True
        elif el_type == 'weather' and (period.get('has_cb') or period.get('weather')):
            has_match = True

        if has_match:
            if g['kind'] == 'BECMG':
                to_h = g['to_utc']
                if to_h is not None:
                    return _build_datetime(ref_time, None, to_h)
            elif g['kind'] == 'FM':
                from_h = g['from_utc']
                if from_h is not None:
                    return _build_datetime(ref_time, None, from_h)
            return None

    return None


def parse_warning_elements(warning_text: str) -> List[WeatherElement]:
    """Parse a weather warning string into WeatherElements.

    Warning elements have source='WARNING' and no times (valid always).
    Extracts visibility, wind, and weather phenomena from free text.
    """
    elements = []
    if not warning_text:
        return elements

    warn_upper = warning_text.upper()

    # CB / TS
    if re.search(r'\bCB\b', warn_upper) or 'THUNDERSTORM' in warn_upper or re.search(r'\bTS\b', warn_upper):
        elements.append(WeatherElement(
            type='weather', value={'code': 'CB'}, source='WARNING', raw=warning_text
        ))

    # Visibility
    vis_m = None
    vis_patterns = [
        r'VIS(?:IBILITY)?\s+(?:BELOW\s+|<\s*|OF\s+)?(\d+)\s*(?:M(?:ETERS?)?|OR\s+LESS)',
        r'VIS(?:IBILITY)?\s+(?:BELOW\s+|<\s*|OF\s+)?(\d+(?:\.\d+)?)\s*KM',
        r'VIS(?:IBILITY)?\s+(\d+)\b',
        r'(\d+)\s*(?:M\b|METERS?)\s+(?:OR\s+LESS|VISIBILITY)',
    ]
    for vp in vis_patterns:
        vm = re.search(vp, warn_upper)
        if vm:
            val = float(vm.group(1))
            if 'KM' in vm.group(0):
                val *= 1000
            elif val < 100:
                val *= 1000
            vis_m = int(val)
            break

    if vis_m is None and any(x in warn_upper for x in
                              ['DUST STORM', 'SANDSTORM', 'SAND STORM', 'BLDU', 'BLSA']):
        vis_m = 1000

    if vis_m is not None:
        elements.append(WeatherElement(
            type='visibility', value={'meters': vis_m}, source='WARNING', raw=warning_text
        ))

    # Wind
    wind_patterns = [
        r'(?:WIND|GUST)S?\s+(?:EXCEEDING\s+|ABOVE\s+|>?\s*)(\d+)\s*(?:KT|KNOTS?)?',
        r'(\d+)\s*(?:KT|KNOTS?)\s+(?:WIND|GUST)',
    ]
    for wp in wind_patterns:
        wm = re.search(wp, warn_upper)
        if wm:
            elements.append(WeatherElement(
                type='wind',
                value={'direction': None, 'speed': int(wm.group(1)), 'gust': None},
                source='WARNING', raw=warning_text
            ))
            break

    # Dust/sand weather
    if any(x in warn_upper for x in ['BLDU', 'BLSA', 'DUST', 'SAND']):
        if not any(el.value.get('code') == 'BLDU' for el in elements):
            elements.append(WeatherElement(
                type='weather', value={'code': 'BLDU'}, source='WARNING', raw=warning_text
            ))

    return elements


def parse_pirep_elements(pirep_text: str,
                         report_time: Optional[datetime] = None,
                         elevation_ft: int = 0) -> List[WeatherElement]:
    """Parse a PIREP string into WeatherElements.

    PIREP format: UA /OV location /FL alt /TP type /SK clouds /WX weather /FV vis
    Elements have source='PIREP', valid_from=report_time, valid_to=None.

    PIREP cloud heights are AMSL. Convert to AGL by subtracting elevation_ft
    so they're in the same reference as METAR/TAF (AGL).
    """
    elements = []
    if not pirep_text:
        return elements

    if report_time is None:
        report_time = datetime.now(timezone.utc)

    upper = pirep_text.upper()

    # Sky condition: /SK BKN040, /SK OVC010CB
    sk_match = re.search(r'/SK\s+([A-Z0-9\s]+?)(?=/|$)', upper)
    if sk_match:
        for cm in re.finditer(r'(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?', sk_match.group(1)):
            cb = cm.group(3) == 'CB' if cm.group(3) else False
            height_amsl = int(cm.group(2)) * 100
            height_agl = max(0, height_amsl - elevation_ft)  # PIREP is AMSL → convert to AGL
            elements.append(WeatherElement(
                type='cloud',
                value={'coverage': cm.group(1), 'height_ft': height_agl, 'cb': cb},
                source='PIREP', valid_from=report_time, valid_to=None,
                raw=cm.group(0)
            ))

    # Weather: /WX TS, /WX BLDU
    wx_match = re.search(r'/WX\s+([A-Z\s+\-]+?)(?=/|$)', upper)
    if wx_match:
        for code in wx_match.group(1).strip().split():
            code = code.strip('+-')
            if len(code) >= 2:
                elements.append(WeatherElement(
                    type='weather', value={'code': code}, source='PIREP',
                    valid_from=report_time, valid_to=None, raw=code
                ))

    # Flight visibility: /FV 3SM, /FV 5000M
    fv_match = re.search(r'/FV\s+(\d+)\s*(SM|KM|M)?', upper)
    if fv_match:
        val = int(fv_match.group(1))
        unit = fv_match.group(2) or ''
        if 'SM' in unit:
            val = int(val * 1609)
        elif 'KM' in unit:
            val *= 1000
        elif val < 100:
            val *= 1000
        elements.append(WeatherElement(
            type='visibility', value={'meters': val}, source='PIREP',
            valid_from=report_time, valid_to=None, raw=fv_match.group(0)
        ))

    # CB anywhere in PIREP
    if re.search(r'\bCB\b|\bTS\b|THUNDERSTORM', upper):
        codes = {el.value.get('code') for el in elements if el.type == 'weather'}
        if 'CB' not in codes and 'TS' not in codes:
            elements.append(WeatherElement(
                type='weather', value={'code': 'CB'}, source='PIREP',
                valid_from=report_time, valid_to=None, raw='CB'
            ))

    return elements
