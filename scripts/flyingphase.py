#!/usr/bin/env python3
"""
KFAA Flying Phase Determination Tool
Parses METAR/TAF for OEKF and determines flying phase based on LOP Table 5-4.

Improvements v2:
- Enhanced METAR parsing (CAVOK, NSC, SKC, NCD, variable wind, RVR, P6SM)
- Full TAF period parsing (BECMG, TEMPO)
- Accurate phase determination with checkmarks
- Runway headings from airfield_data.json
- Improved alternate suitability checking
- Better divert fuel calculations with wind components
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# TAF cache configuration
TAF_CACHE_DIR = "/tmp/flyingphase_taf_cache"
TAF_CACHE_EXPIRY_SECS = 1800  # 30 minutes


class METARParser:
    """Parse METAR strings and extract weather elements."""
    
    def __init__(self, metar_string: str):
        self.raw = metar_string.strip()
        self.icao = None
        self.wind_dir = None
        self.wind_speed = None
        self.wind_gust = None
        self.wind_variable_from = None
        self.wind_variable_to = None
        self.visibility_m = None
        self.clouds = []
        self.weather = []
        self.temp = None
        self.dewpoint = None
        self.qnh = None
        self.cavok = False
        self.rvr = []
        self.remarks = ""
        self.cb_details = []  # List of CB observations with distance/direction
        self.has_ts_weather = False  # TS in weather group
        self.parse_warnings = []  # Track what couldn't be parsed
        self.parse()
    
    def validate(self) -> List[str]:
        """Check for missing critical fields and return list of issues."""
        issues = []
        if self.wind_speed is None:
            issues.append("❌ Wind group not found — expected format like 33012KT or VRB03KT")
        if self.visibility_m is None and not self.cavok:
            issues.append("❌ Visibility not found — expected 4-digit meters (e.g. 9999, 3000) or CAVOK")
        has_sky_code = any(code in self.raw.upper() for code in ['NSC', 'SKC', 'NCD', 'CLR'])
        if not self.clouds and not self.cavok and not has_sky_code:
            issues.append("⚠️ No cloud groups found — expected format like FEW040, SCT080, BKN015, OVC003")
        if self.temp is None:
            issues.append("⚠️ Temperature not found — expected format like 22/10 or M02/M05")
        # Add any parse warnings
        issues.extend(self.parse_warnings)
        return issues
    
    @staticmethod
    def _is_weather_token(token: str) -> bool:
        """Check if a token is a valid weather phenomenon group.
        
        Weather tokens are composed of 2-character codes (with optional +/- prefix).
        e.g. TSRA = TS+RA, BLDU = BL+DU, +SHRA = SH+RA, -DZ = DZ
        """
        t = token.upper().lstrip('+-')
        if len(t) < 2 or len(t) > 8 or len(t) % 2 != 0:
            return False
        wx_codes = {
            'MI', 'BC', 'PR', 'DR', 'BL', 'SH', 'TS', 'FZ',  # Descriptors
            'DZ', 'RA', 'SN', 'SG', 'IC', 'PL', 'GR', 'GS',  # Precipitation
            'BR', 'FG', 'FU', 'VA', 'DU', 'SA', 'HZ', 'PO',  # Obscuration
            'SQ', 'FC', 'SS', 'DS',                             # Other
        }
        return all(t[i:i+2] in wx_codes for i in range(0, len(t), 2))
    
    def parse(self):
        """Parse METAR string using order-independent token classification.
        
        Tokens are classified by pattern rather than position, so elements
        like NSC, visibility, and clouds can appear in any order.
        Only the ICAO code and timestamp are expected near the start.
        """
        parts = self.raw.split()
        
        # Remove METAR/SPECI prefix
        if parts and parts[0] in ('METAR', 'SPECI'):
            parts = parts[1:]
        
        # --- Split observation from trend/remarks ---
        # Everything after NOSIG/TEMPO/BECMG is trend forecast (not observation)
        # Everything after RMK is remarks
        obs_parts = []
        trend_start = None
        rmk_start = None
        
        for i, part in enumerate(parts):
            if part == 'RMK' and rmk_start is None:
                rmk_start = i
                break
            if part in ('NOSIG', 'TEMPO', 'BECMG') and trend_start is None:
                trend_start = i
                break
            obs_parts.append(part)
        
        # Capture remarks
        if rmk_start is not None:
            self.remarks = ' '.join(parts[rmk_start + 1:])
        elif trend_start is not None:
            # Check for RMK after trend
            for i in range(trend_start, len(parts)):
                if parts[i] == 'RMK':
                    self.remarks = ' '.join(parts[i + 1:])
                    break
        
        # Track which tokens have been consumed
        consumed = set()
        
        # --- 1. ICAO code (4 uppercase letters, not a weather token) ---
        # Only look in first 3 tokens to avoid false matches
        for i, part in enumerate(obs_parts[:3]):
            if (len(part) == 4 and part.isalpha() and part.isupper()
                    and not self._is_weather_token(part)
                    and part not in ('AUTO', 'CAVOK')):
                self.icao = part
                consumed.add(i)
                break
        
        if not self.icao:
            self.icao = 'OEKF'
        
        # --- 2. Timestamp (DDHHmmZ) ---
        self.obs_day = None
        self.obs_hour = None
        self.obs_minute = None
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            if re.match(r'^\d{6}Z$', part):
                self.obs_day = int(part[:2])
                self.obs_hour = int(part[2:4])
                self.obs_minute = int(part[4:6])
                consumed.add(i)
                break
        
        if self.obs_hour is None:
            from datetime import datetime as _dt, timezone as _tz
            _now = _dt.now(_tz.utc)
            self.obs_day = _now.day
            self.obs_hour = _now.hour
            self.obs_minute = _now.minute
        
        # --- 3. AUTO / COR ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            if part in ('AUTO', 'COR'):
                consumed.add(i)
        
        # --- 4. Wind (dddssKT, dddssGggKT, VRBssKT, 00000KT) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            match = re.match(r'^(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?KT$', part)
            if match:
                if match.group(1) == 'VRB':
                    self.wind_dir = None
                elif match.group(1) == '000':
                    self.wind_dir = 0
                    self.wind_speed = 0
                else:
                    self.wind_dir = int(match.group(1))
                
                if match.group(1) != '000':
                    self.wind_speed = int(match.group(2))
                    if match.group(4):
                        self.wind_gust = int(match.group(4))
                consumed.add(i)
                break
        
        # --- 5. Variable wind direction (dddVddd) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            match = re.match(r'^(\d{3})V(\d{3})$', part)
            if match:
                self.wind_variable_from = int(match.group(1))
                self.wind_variable_to = int(match.group(2))
                consumed.add(i)
                break
        
        # --- 6. Visibility (4-digit meters, CAVOK, statute miles) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            if part == 'CAVOK':
                self.cavok = True
                self.visibility_m = 10000
                consumed.add(i)
                break
            elif re.match(r'^P?\d+SM$', part):
                sm_match = re.match(r'^P?(\d+)SM$', part)
                sm = int(sm_match.group(1))
                self.visibility_m = int(sm * 1609)
                consumed.add(i)
                break
            elif re.match(r'^\d{4}$', part):
                vis = int(part)
                self.visibility_m = 10000 if vis == 9999 else vis
                consumed.add(i)
                break
        
        # --- 7. RVR (R33L/1200M, R15L/P2000) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            if part.startswith('R'):
                match = re.match(r'^R(\d{2}[LCR]?)/([PM]?\d{4})', part)
                if match:
                    self.rvr.append({
                        'runway': match.group(1),
                        'distance_m': match.group(2)
                    })
                    consumed.add(i)
        
        # --- 8. Weather phenomena (BR, FG, TSRA, BLDU, +SHRA, etc.) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            upper = part.upper()
            # Standalone CB or TCU
            if upper in ('CB', 'TCU'):
                if upper == 'CB':
                    self.weather.append('CB')
                consumed.add(i)
                continue
            if self._is_weather_token(part):
                self.weather.append(part)
                if 'TS' in upper:
                    self.has_ts_weather = True
                consumed.add(i)
        
        # --- 9. Clouds (FEW040, SCT020, BKN015CB, OVC010, NSC, SKC, NCD, CLR) ---
        clear_sky_codes = {'NSC', 'SKC', 'NCD', 'CLR'}
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            if part in clear_sky_codes:
                consumed.add(i)
                continue
            match = re.match(r'^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?$', part)
            if match:
                coverage = match.group(1)
                height_ft = int(match.group(2)) * 100
                cloud_type = match.group(3) if match.group(3) else None
                self.clouds.append({
                    'coverage': coverage,
                    'height_ft': height_ft,
                    'type': cloud_type
                })
                consumed.add(i)
        
        # --- 10. Temperature / Dewpoint (22/10, M02/M05) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            match = re.match(r'^(M?\d{2})/(M?\d{2})$', part)
            if match:
                self.temp = int(match.group(1).replace('M', '-'))
                self.dewpoint = int(match.group(2).replace('M', '-'))
                consumed.add(i)
                break
        
        # --- 11. QNH (Q1013 or A2992) ---
        for i, part in enumerate(obs_parts):
            if i in consumed:
                continue
            match = re.match(r'^Q(\d{4})$', part)
            if match:
                self.qnh = int(match.group(1))
                consumed.add(i)
                break
            match = re.match(r'^A(\d{4})$', part)
            if match:
                self.qnh = int(match.group(1))
                consumed.add(i)
                break
        
        # Parse CB details from remarks and full METAR
        self._parse_cb_details()
    
    def _parse_cb_details(self):
        """Parse CB distance/direction from METAR remarks and weather groups."""
        full_text = self.raw.upper()
        
        # Pattern: CB followed by direction/distance
        # Examples: "CB NW MOV E", "CB DSNT W", "CB OHD MOV NE", "CB NW-N 25NM"
        # Longer patterns first to avoid partial matches (NW before N, etc.)
        directions = r'(?:NE|NW|SE|SW|N|E|W|S|OHD|DSNT|VC)'
        
        # CB with direction: "CB NW MOV E", "CB DSNT SW"
        cb_pattern = re.compile(
            r'\bCB\s+(' + directions + r'(?:[-/]' + directions + r')?)'
            r'(?:\s+(\d+)\s*NM)?'
            r'(?:\s+MOV\s+(' + directions + r'))?',
            re.IGNORECASE
        )
        
        for match in cb_pattern.finditer(full_text):
            detail = {
                'location': match.group(1),
                'distance_nm': int(match.group(2)) if match.group(2) else None,
                'movement': match.group(3)
            }
            # DSNT = distant (typically 10-30 NM); VC = vicinity (5-10 NM)
            if detail['distance_nm'] is None:
                loc = detail['location'].upper()
                if 'DSNT' in loc:
                    detail['distance_nm'] = 25  # Estimate
                elif 'VC' in loc:
                    detail['distance_nm'] = 8   # Estimate
                elif 'OHD' in loc:
                    detail['distance_nm'] = 0   # Overhead
            
            self.cb_details.append(detail)
        
        # Also check for "TS" in weather groups (already sets has_ts_weather in parse())
        # And check for TCU in cloud layers (towering cumulus - precursor to CB)
    
    def get_effective_wind_speed(self) -> int:
        """Return effective wind speed (gusts count as wind speed)."""
        if self.wind_speed is None:
            return 0
        return self.wind_gust if self.wind_gust else self.wind_speed
    
    def get_ceiling_ft(self) -> Optional[int]:
        """Return ceiling (lowest BKN or OVC layer)."""
        for cloud in self.clouds:
            if cloud['coverage'] in ['BKN', 'OVC']:
                return cloud['height_ft']
        return None
    
    def get_lowest_cloud_ft(self) -> Optional[int]:
        """Return lowest cloud layer of any type."""
        if self.clouds:
            return min(c['height_ft'] for c in self.clouds)
        return None
    
    def has_cb(self) -> bool:
        """Check if CB (cumulonimbus) is present in cloud layers, weather, or remarks."""
        for cloud in self.clouds:
            if cloud.get('type') == 'CB':
                return True
        # TS (thunderstorm) in weather implies CB activity
        if self.has_ts_weather:
            return True
        # CB mentioned in remarks
        if self.cb_details:
            return True
        return False
    
    def has_cb_within_nm(self, max_nm: int = 30) -> bool:
        """Check if CB is reported within a given distance (NM)."""
        # CB in cloud layers = overhead
        for cloud in self.clouds:
            if cloud.get('type') == 'CB':
                return True
        # TS in weather = at the station
        if self.has_ts_weather:
            return True
        # Check CB details from remarks
        for cb in self.cb_details:
            dist = cb.get('distance_nm')
            if dist is not None and dist <= max_nm:
                return True
            elif dist is None:
                # Unknown distance, assume could be close
                return True
        return False
    
    def get_cb_warnings(self) -> List[str]:
        """Get human-readable CB warning strings."""
        warnings = []
        for cloud in self.clouds:
            if cloud.get('type') == 'CB':
                warnings.append(f"CB in cloud layer at {cloud['height_ft']}ft")
        if self.has_ts_weather:
            ts_wx = [w for w in self.weather if 'TS' in w.upper()]
            warnings.append(f"Thunderstorm activity: {' '.join(ts_wx)}")
        for cb in self.cb_details:
            parts = [f"CB {cb['location']}"]
            if cb.get('distance_nm') is not None:
                parts.append(f"{cb['distance_nm']}NM")
            if cb.get('movement'):
                parts.append(f"MOV {cb['movement']}")
            warnings.append(' '.join(parts))
        return warnings

    def apply_taf_overlay(self, taf_overrides: dict) -> List[str]:
        """
        Overlay worst-case TAF planning window conditions onto this METAR.
        
        Only overrides values where the TAF is MORE RESTRICTIVE than current METAR.
        Returns list of factors that were applied.
        """
        factors = []
        
        # Visibility: take lowest
        ov_vis = taf_overrides.get('visibility_m')
        if ov_vis is not None and (self.visibility_m is None or ov_vis < self.visibility_m):
            self.visibility_m = ov_vis
            factors.append(f'Vis → {ov_vis}m (TAF)')
        
        # Wind: take highest effective wind
        ov_wind_eff = (taf_overrides.get('wind_gust') or taf_overrides.get('wind_speed') or 0)
        my_wind_eff = self.get_effective_wind_speed()
        if ov_wind_eff > my_wind_eff:
            self.wind_dir = taf_overrides.get('wind_dir', self.wind_dir)
            self.wind_speed = taf_overrides.get('wind_speed', self.wind_speed)
            self.wind_gust = taf_overrides.get('wind_gust', self.wind_gust)
            factors.append(f'Wind → {self.wind_dir}/{self.wind_speed}'
                          f'{"G" + str(self.wind_gust) if self.wind_gust else ""}kt (TAF)')
        
        # Clouds/ceiling: merge — add any TAF clouds that are lower
        for taf_cloud in taf_overrides.get('clouds', []):
            # Check if this cloud is lower than any existing METAR cloud
            already_covered = False
            for mc in self.clouds:
                if mc['height_ft'] <= taf_cloud['height_ft'] and \
                   mc['coverage'] >= taf_cloud['coverage']:  # BKN > SCT etc (string compare works)
                    already_covered = True
                    break
            if not already_covered:
                self.clouds.append(taf_cloud)
                factors.append(f"Cloud → {taf_cloud['coverage']}{taf_cloud['height_ft']//100:03d} (TAF)")
        
        # Re-sort clouds by height
        self.clouds.sort(key=lambda c: c['height_ft'])
        
        # CB
        if taf_overrides.get('has_cb') and not self.has_cb():
            self.weather.append('CB')
            self.has_ts_weather = True
            factors.append('CB forecast (TAF)')
        
        # Weather phenomena
        for wx in taf_overrides.get('weather', []):
            if wx not in self.weather:
                self.weather.append(wx)
                if wx == 'TS':
                    self.has_ts_weather = True
        
        return factors


class TAFParser:
    """Parse TAF strings and extract forecast periods."""
    
    def __init__(self, taf_string: str):
        self.raw = taf_string.strip()
        self.icao = None
        self.base_period = None
        self.becmg_periods = []
        self.tempo_periods = []
        self.fm_periods = []  # FM (From) groups
        self.parse()
    
    def parse(self):
        """Parse TAF string into periods."""
        # Remove "TAF" prefix
        text = self.raw
        if text.startswith('TAF '):
            text = text[4:]
        
        # Extract ICAO
        parts = text.split()
        for part in parts[:3]:
            if len(part) == 4 and part.isalpha() and part.isupper():
                if part not in ['BECMG', 'TEMPO', 'PROB']:
                    self.icao = part
                    break
        
        # Split into base, BECMG, TEMPO, and FM groups
        # Base is everything before first BECMG/TEMPO/FM
        becmg_pattern = r'BECMG \d{4}/\d{4}'
        tempo_pattern = r'TEMPO \d{4}/\d{4}'
        fm_pattern = r'FM\d{6}'
        
        # Combined pattern to find next period marker (for boundary detection)
        next_period_re = re.compile(r'(BECMG \d{4}/\d{4}|TEMPO \d{4}/\d{4}|FM\d{6})')
        
        # Find all BECMG periods
        for match in re.finditer(becmg_pattern, text):
            start = match.start()
            end = len(text)
            
            # Look for next period marker after this one
            next_match = next_period_re.search(text, start + len(match.group()))
            if next_match:
                end = next_match.start()
            
            period_text = text[start:end].strip()
            self.becmg_periods.append(self._parse_period(period_text))
        
        # Find all TEMPO periods
        for match in re.finditer(tempo_pattern, text):
            start = match.start()
            end = len(text)
            
            next_match = next_period_re.search(text, start + len(match.group()))
            if next_match:
                end = next_match.start()
            
            period_text = text[start:end].strip()
            self.tempo_periods.append(self._parse_period(period_text))
        
        # Find all FM periods
        for match in re.finditer(fm_pattern, text):
            start = match.start()
            end = len(text)
            
            next_match = next_period_re.search(text, start + len(match.group()))
            if next_match:
                end = next_match.start()
            
            period_text = text[start:end].strip()
            self.fm_periods.append(self._parse_period(period_text))
        
        # Base period is everything before first BECMG/TEMPO/FM
        base_end = len(text)
        first_marker = next_period_re.search(text)
        if first_marker:
            base_end = first_marker.start()
        
        base_text = text[:base_end].strip()
        self.base_period = self._parse_period(base_text)
    
    def _parse_period(self, period_text: str) -> dict:
        """Parse a single TAF period."""
        result = {
            'raw': period_text,
            'valid_from_utc': None,  # Hour (0-23) UTC
            'valid_to_utc': None,    # Hour (0-23) UTC
            'wind_dir': None,
            'wind_speed': None,
            'wind_gust': None,
            'visibility_m': None,
            'clouds': [],
            'weather': [],
            'has_cb': False
        }
        
        # Extract validity period times
        # BECMG/TEMPO: "BECMG 3106/3108" or "TEMPO 3112/3118"
        time_match = re.search(r'(?:BECMG|TEMPO)\s+\d{2}(\d{2})/\d{2}(\d{2})', period_text)
        if time_match:
            result['valid_from_utc'] = int(time_match.group(1))
            result['valid_to_utc'] = int(time_match.group(2))
        
        # FM group: "FM310800" → from 08Z
        fm_match = re.match(r'FM\d{2}(\d{2})(\d{2})', period_text)
        if fm_match:
            result['valid_from_utc'] = int(fm_match.group(1))
            # FM periods run until next FM or end of TAF (set to 24 as sentinel)
            result['valid_to_utc'] = 24
        
        # Base TAF validity: "3100/3124" or "0100/0206"
        base_match = re.search(r'^\s*\w{4}\s+\d{6}Z\s+\d{2}(\d{2})/\d{2}(\d{2})', period_text)
        if base_match:
            result['valid_from_utc'] = int(base_match.group(1))
            result['valid_to_utc'] = int(base_match.group(2))
        
        # Wind
        wind_pattern = r'(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?KT'
        match = re.search(wind_pattern, period_text)
        if match:
            if match.group(1) != 'VRB':
                result['wind_dir'] = int(match.group(1))
            result['wind_speed'] = int(match.group(2))
            if match.group(4):
                result['wind_gust'] = int(match.group(4))
        
        # Visibility
        vis_pattern = r'\s(\d{4})\s'
        match = re.search(vis_pattern, period_text)
        if match:
            vis = int(match.group(1))
            result['visibility_m'] = 10000 if vis == 9999 else vis
        
        if 'CAVOK' in period_text:
            result['visibility_m'] = 10000
        
        # Clouds
        cloud_pattern = r'(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?'
        for match in re.finditer(cloud_pattern, period_text):
            coverage = match.group(1)
            height_ft = int(match.group(2)) * 100
            cloud_type = match.group(3)
            
            result['clouds'].append({
                'coverage': coverage,
                'height_ft': height_ft,
                'type': cloud_type
            })
            
            if cloud_type == 'CB':
                result['has_cb'] = True
        
        # Check for CB in weather
        if 'CB' in period_text:
            result['has_cb'] = True
        
        # Weather phenomena
        weather_codes = ['BR', 'FG', 'HZ', 'RA', 'SN', 'TS', 'DZ', 'SH', 'GR', 'GS']
        for code in weather_codes:
            if code in period_text:
                result['weather'].append(code)
        
        return result
    
    def get_all_periods(self) -> List[dict]:
        """Get all periods (base + BECMG + TEMPO + FM)."""
        periods = []
        if self.base_period:
            periods.append(('BASE', self.base_period))
        for p in self.becmg_periods:
            periods.append(('BECMG', p))
        for p in self.tempo_periods:
            periods.append(('TEMPO', p))
        for p in self.fm_periods:
            periods.append(('FM', p))
        return periods

    def get_planning_window(self, now_hour: int, now_min: int, window_min: int = 30) -> dict:
        """
        Get worst-case TAF conditions over [now, now+window_min].
        
        Checks all BECMG/TEMPO/FM periods that overlap the window.
        Returns dict with worst-case overrides (only fields that are worse than None).
        """
        window_start = now_hour + now_min / 60.0
        window_end = window_start + window_min / 60.0
        
        overrides = {
            'visibility_m': None,
            'wind_dir': None,
            'wind_speed': None,
            'wind_gust': None,
            'ceiling_ft': None,
            'lowest_cloud_ft': None,
            'clouds': [],
            'has_cb': False,
            'weather': [],
            'factors': []  # Human-readable list of what TAF contributed
        }
        
        def _period_overlaps(p_from, p_to):
            """Check if TAF period [p_from, p_to) overlaps [window_start, window_end]."""
            if p_from is None:
                return True  # No time info → assume could overlap
            pf = float(p_from)
            pt = float(p_to) if p_to is not None else pf + 24
            # Handle day wrap (e.g., valid_from=22, valid_to=6 means 22Z-06Z next day)
            ws = window_start % 24
            we = window_end % 24
            if pt <= pf:
                pt += 24
            if we <= ws:
                we += 24
            return pf < we and pt > ws
        
        for period_type, period in self.get_all_periods():
            if period_type == 'BASE':
                # Base period always applies (it's the background forecast)
                pass
            else:
                p_from = period.get('valid_from_utc')
                p_to = period.get('valid_to_utc')
                if not _period_overlaps(p_from, p_to):
                    continue
            
            # Visibility: take lowest
            p_vis = period.get('visibility_m')
            if p_vis is not None:
                if overrides['visibility_m'] is None or p_vis < overrides['visibility_m']:
                    overrides['visibility_m'] = p_vis
                    if period_type != 'BASE':
                        overrides['factors'].append(f'{period_type}: vis {p_vis}m')
            
            # Wind: take highest effective (gust or sustained)
            p_wind_eff = period.get('wind_gust') or period.get('wind_speed') or 0
            current_eff = overrides['wind_gust'] or overrides['wind_speed'] or 0
            if p_wind_eff > current_eff:
                overrides['wind_dir'] = period.get('wind_dir', overrides['wind_dir'])
                overrides['wind_speed'] = period.get('wind_speed', overrides['wind_speed'])
                overrides['wind_gust'] = period.get('wind_gust')
                if period_type != 'BASE':
                    g_str = f"G{period.get('wind_gust')}" if period.get('wind_gust') else ""
                    overrides['factors'].append(
                        f"{period_type}: wind {period.get('wind_dir', '???')}/"
                        f"{period.get('wind_speed', '?')}{g_str}kt"
                    )
            
            # Ceiling/clouds: take lowest ceiling
            for cloud in period.get('clouds', []):
                if cloud['coverage'] in ['BKN', 'OVC']:
                    h = cloud['height_ft']
                    if overrides['ceiling_ft'] is None or h < overrides['ceiling_ft']:
                        overrides['ceiling_ft'] = h
                        if period_type != 'BASE':
                            overrides['factors'].append(f'{period_type}: ceiling {h}ft')
                # Track lowest cloud of any type
                h = cloud['height_ft']
                if overrides['lowest_cloud_ft'] is None or h < overrides['lowest_cloud_ft']:
                    overrides['lowest_cloud_ft'] = h
                overrides['clouds'].append(cloud)
            
            # CB / weather
            if period.get('has_cb'):
                overrides['has_cb'] = True
                if period_type != 'BASE':
                    overrides['factors'].append(f'{period_type}: CB forecast')
            
            for wx in period.get('weather', []):
                if wx not in overrides['weather']:
                    overrides['weather'].append(wx)
        
        return overrides
    
    def check_deterioration(self, vis_limit_m: int = 5000, ceiling_limit_ft: int = 1500) -> Tuple[bool, str]:
        """Check if any period shows deterioration below limits."""
        for period_type, period in self.get_all_periods():
            if period['visibility_m'] and period['visibility_m'] < vis_limit_m:
                return True, f"{period_type}: Vis {period['visibility_m']}m < {vis_limit_m}m"
            
            # Check ceiling
            for cloud in period['clouds']:
                if cloud['coverage'] in ['BKN', 'OVC'] and cloud['height_ft'] < ceiling_limit_ft:
                    return True, f"{period_type}: Ceiling {cloud['height_ft']}ft < {ceiling_limit_ft}ft"
        
        return False, ""
    
    def get_sortie_window_conditions(self, sortie_utc_hour: int) -> dict:
        """
        Analyse TAF for a ±1 hour window around sortie time.
        
        Args:
            sortie_utc_hour: Sortie time in UTC hours (0-23)
        
        Returns:
            dict with worst-case conditions and analysis
        """
        window_start = (sortie_utc_hour - 1) % 24
        window_end = (sortie_utc_hour + 1) % 24
        
        overlapping = []
        
        def _hours_overlap(p_from, p_to, w_from, w_end):
            """Check if period [p_from, p_to) overlaps window [w_from, w_end]."""
            if p_from is None or p_to is None:
                return True  # If no time info, assume it could overlap
            # Handle wrap-around midnight
            if p_to <= p_from:
                p_to += 24
            if w_end <= w_from:
                w_end += 24
            return p_from < w_end + 1 and p_to > w_from  # +1 for inclusive end
        
        for period_type, period in self.get_all_periods():
            p_from = period.get('valid_from_utc')
            p_to = period.get('valid_to_utc')
            
            if _hours_overlap(p_from, p_to, window_start, window_end):
                overlapping.append((period_type, period))
        
        if not overlapping:
            return {'applicable': False, 'reason': 'No TAF periods cover sortie window'}
        
        # Find worst-case conditions across overlapping periods
        worst_vis = None
        worst_ceiling = None
        worst_wind = 0
        worst_gust = 0
        has_cb = False
        has_ts = False
        weather_set = set()
        deteriorating = False
        
        base_vis = None
        base_ceiling = None
        
        for period_type, period in overlapping:
            vis = period.get('visibility_m')
            if vis is not None:
                if worst_vis is None or vis < worst_vis:
                    worst_vis = vis
                if period_type == 'BASE':
                    base_vis = vis
            
            # Ceiling from BKN/OVC layers
            for cloud in period.get('clouds', []):
                if cloud['coverage'] in ['BKN', 'OVC']:
                    ceil = cloud['height_ft']
                    if worst_ceiling is None or ceil < worst_ceiling:
                        worst_ceiling = ceil
                    if period_type == 'BASE':
                        if base_ceiling is None or ceil < base_ceiling:
                            base_ceiling = ceil
                    break
            
            wind = period.get('wind_speed', 0) or 0
            gust = period.get('wind_gust', 0) or 0
            effective = gust if gust else wind
            if effective > worst_wind:
                worst_wind = effective
            if gust > worst_gust:
                worst_gust = gust
            
            if period.get('has_cb'):
                has_cb = True
            
            for wx in period.get('weather', []):
                weather_set.add(wx)
                if wx == 'TS':
                    has_ts = True
        
        # Check for deterioration within window
        if base_vis is not None and worst_vis is not None and worst_vis < base_vis:
            deteriorating = True
        if base_ceiling is not None and worst_ceiling is not None and worst_ceiling < base_ceiling:
            deteriorating = True
        
        # Build summary
        parts = []
        if worst_vis is not None:
            parts.append(f"Vis ≥{worst_vis}m")
        if worst_ceiling is not None:
            parts.append(f"Ceil ≥{worst_ceiling}ft")
        if worst_wind > 0:
            wind_str = f"Wind ≤{worst_wind}kt"
            if worst_gust > 0:
                wind_str += f" G{worst_gust}"
            parts.append(wind_str)
        if has_cb:
            parts.append("⚠️ CB")
        if has_ts:
            parts.append("⚠️ TS")
        if weather_set - {'TS'}:
            parts.append(f"Wx: {','.join(sorted(weather_set - {'TS'}))}")
        
        summary = " | ".join(parts) if parts else "No significant weather"
        
        return {
            'applicable': True,
            'worst_vis_m': worst_vis,
            'worst_ceiling_ft': worst_ceiling,
            'worst_wind_kt': worst_wind,
            'worst_gust_kt': worst_gust,
            'has_cb': has_cb,
            'has_ts': has_ts,
            'weather': sorted(weather_set),
            'deteriorating': deteriorating,
            'overlapping_periods': len(overlapping),
            'summary': summary
        }


def calculate_wind_components(wind_dir: int, wind_speed: int, runway_heading: int) -> Tuple[float, float]:
    """
    Calculate crosswind and headwind/tailwind components.
    
    Returns:
        (crosswind, headwind) - headwind negative means tailwind
    """
    angle_diff = abs(wind_dir - runway_heading)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    
    angle_rad = math.radians(angle_diff)
    crosswind = abs(wind_speed * math.sin(angle_rad))
    headwind = wind_speed * math.cos(angle_rad)
    
    return crosswind, headwind


def determine_phase(metar: METARParser, runway_heading: int, airfield_data: dict) -> dict:
    """
    Determine flying phase based on METAR and LOP Table 5-4.
    
    Returns dict with phase, restrictions, conditions, and check results.
    """
    result = {
        'phase': None,
        'conditions': {},
        'restrictions': {},
        'reasons': [],
        'checks': {}  # Pass/fail for each phase
    }
    
    # Get wind components
    effective_wind = metar.get_effective_wind_speed()
    
    if metar.wind_dir is not None:
        crosswind, headwind = calculate_wind_components(
            metar.wind_dir, effective_wind, runway_heading
        )
    else:
        # Variable wind - assume worst case (all crosswind)
        crosswind = effective_wind
        headwind = 0
    
    tailwind = abs(headwind) if headwind < 0 else 0
    headwind_component = headwind if headwind > 0 else 0
    
    result['conditions'] = {
        'visibility_m': metar.visibility_m,
        'visibility_km': metar.visibility_m / 1000 if metar.visibility_m else None,
        'ceiling_ft': metar.get_ceiling_ft(),
        'lowest_cloud_ft': metar.get_lowest_cloud_ft(),
        'clouds': metar.clouds,
        'wind_dir': metar.wind_dir,
        'wind_speed': metar.wind_speed,
        'wind_gust': metar.wind_gust,
        'effective_wind': effective_wind,
        'crosswind': round(crosswind, 1),
        'headwind': round(headwind_component, 1),
        'tailwind': round(tailwind, 1),
        'temp': metar.temp,
        'cavok': metar.cavok,
        'has_cb': metar.has_cb(),
        'has_ts': metar.has_ts_weather,
        'cb_details': metar.cb_details,
        'cb_warnings': metar.get_cb_warnings()
    }
    
    vis_km = result['conditions']['visibility_km']
    ceiling = result['conditions']['ceiling_ft']
    lowest_cloud = result['conditions']['lowest_cloud_ft']
    
    # Check RECALL conditions first (most restrictive)
    recall_checks = []
    
    if effective_wind > 35:
        result['phase'] = 'RECALL'
        result['reasons'].append(f'⚠️ Wind exceeds limits ({effective_wind}kt > 35kt)')
        return result
    
    if metar.has_cb_within_nm(30):
        result['phase'] = 'RECALL'
        cb_warnings = metar.get_cb_warnings()
        if cb_warnings:
            result['reasons'].append(f'⚠️ CB within 30NM: {"; ".join(cb_warnings)}')
        else:
            result['reasons'].append('⚠️ CB (cumulonimbus) present')
        return result
    
    # Check HOLD conditions
    hold_checks = []
    
    if metar.temp and metar.temp > 50:
        result['phase'] = 'HOLD'
        result['reasons'].append(f'🌡️ Temperature exceeds 50°C ({metar.temp}°C)')
        return result
    
    if crosswind > 24:
        result['phase'] = 'HOLD'
        result['reasons'].append(f'💨 Crosswind exceeds 24kt ({crosswind:.1f}kt)')
        return result
    
    # Check each phase from most permissive to most restrictive
    
    # UNRESTRICTED: No cloud of ANY type below 8000ft. Above 8000ft, only FEW allowed.
    unrestricted_checks = []
    
    unrestricted_checks.append(('Vis ≥ 8km', vis_km and vis_km >= 8))
    unrestricted_checks.append(('No cloud < 8000ft', lowest_cloud is None or lowest_cloud >= 8000))
    
    # Check for cloud coverage above 8000ft
    no_sct_bkn_ovc = True
    for cloud in metar.clouds:
        if cloud['height_ft'] < 8000:
            no_sct_bkn_ovc = False
            break
        if cloud['coverage'] in ['SCT', 'BKN', 'OVC']:
            no_sct_bkn_ovc = False
            break
    
    unrestricted_checks.append(('Max FEW above 8000ft', no_sct_bkn_ovc))
    unrestricted_checks.append(('Total wind ≤ 25kt', effective_wind <= 25))
    unrestricted_checks.append(('Crosswind ≤ 15kt', crosswind <= 15))
    unrestricted_checks.append(('Tailwind ≤ 5kt', tailwind <= 5))
    
    result['checks']['UNRESTRICTED'] = unrestricted_checks
    
    if all(check[1] for check in unrestricted_checks):
        result['phase'] = 'UNRESTRICTED'
        result['restrictions'] = {
            'solo_cadets': True,
            'first_solo': True
        }
        return result
    
    # RESTRICTED: No cloud of ANY type below 6000ft. Max SCT above 6000ft.
    restricted_checks = []
    
    restricted_checks.append(('Vis ≥ 8km', vis_km and vis_km >= 8))
    restricted_checks.append(('No cloud < 6000ft', lowest_cloud is None or lowest_cloud >= 6000))
    
    # Check for BKN/OVC above 6000ft
    no_bkn_ovc = True
    for cloud in metar.clouds:
        if cloud['height_ft'] < 6000:
            no_bkn_ovc = False
            break
        if cloud['coverage'] in ['BKN', 'OVC']:
            no_bkn_ovc = False
            break
    
    restricted_checks.append(('Max SCT above 6000ft', no_bkn_ovc))
    restricted_checks.append(('Total wind ≤ 25kt', effective_wind <= 25))
    restricted_checks.append(('Crosswind ≤ 15kt', crosswind <= 15))
    restricted_checks.append(('Tailwind ≤ 5kt', tailwind <= 5))
    
    result['checks']['RESTRICTED'] = restricted_checks
    
    if all(check[1] for check in restricted_checks):
        result['phase'] = 'RESTRICTED'
        result['restrictions'] = {
            'solo_cadets': True,
            'solo_note': 'Post-IIC only',
            'first_solo': True
        }
        return result
    
    # FS VFR: No cloud of ANY type below 5000ft
    fs_vfr_checks = []
    
    fs_vfr_checks.append(('Vis ≥ 5km', vis_km and vis_km >= 5))
    fs_vfr_checks.append(('No cloud < 5000ft', lowest_cloud is None or lowest_cloud >= 5000))
    fs_vfr_checks.append(('Total wind ≤ 25kt', effective_wind <= 25))
    fs_vfr_checks.append(('Crosswind ≤ 15kt', crosswind <= 15))
    fs_vfr_checks.append(('Tailwind ≤ 5kt', tailwind <= 5))
    
    result['checks']['FS VFR'] = fs_vfr_checks
    
    if all(check[1] for check in fs_vfr_checks):
        result['phase'] = 'FS VFR'
        result['restrictions'] = {
            'solo_cadets': False,
            'solo_note': 'Not authorized',
            'first_solo': True
        }
        return result
    
    # VFR: Ceiling ≥ 1500ft (for 1000ft vertical clearance), Vis ≥ 5km
    vfr_checks = []
    
    vfr_checks.append(('Vis ≥ 5km', vis_km and vis_km >= 5))
    vfr_checks.append(('Ceiling ≥ 1500ft', ceiling is None or ceiling >= 1500))
    vfr_checks.append(('Total wind ≤ 30kt', effective_wind <= 30))
    vfr_checks.append(('Crosswind ≤ 24kt', crosswind <= 24))
    vfr_checks.append(('Tailwind ≤ 10kt', tailwind <= 10))
    
    result['checks']['VFR'] = vfr_checks
    
    if all(check[1] for check in vfr_checks):
        result['phase'] = 'VFR'
        result['restrictions'] = {
            'solo_cadets': False,
            'first_solo': False
        }
        return result
    
    # IFR: Above approach minimums + 300ft ceiling, +buffer visibility
    # Use OEKF approach data if available, else use placeholder
    approaches = airfield_data.get('OEKF', {}).get('approaches', [])
    
    # Use conservative minimums (ILS CAT I equivalent)
    min_vis_m = 2400  # Conservative (NDB-level)
    min_ceiling_ft = 500  # 200ft DH + 300ft
    
    if approaches:
        # Use best available approach
        for app in approaches:
            app_vis = app['minimums'].get('visibility_m', 800)
            app_ceil = app['minimums'].get('ceiling_ft', 200)
            min_vis_m = min(min_vis_m, app_vis)
            min_ceiling_ft = min(min_ceiling_ft, app_ceil + 300)
    
    ifr_checks = []
    
    ifr_checks.append((f'Vis ≥ {min_vis_m}m', metar.visibility_m and metar.visibility_m >= min_vis_m))
    ifr_checks.append((f'Ceiling ≥ {min_ceiling_ft}ft', ceiling is None or ceiling >= min_ceiling_ft))
    ifr_checks.append(('Total wind ≤ 30kt', effective_wind <= 30))
    ifr_checks.append(('Crosswind ≤ 24kt', crosswind <= 24))
    ifr_checks.append(('Tailwind ≤ 10kt', tailwind <= 10))
    
    result['checks']['IFR'] = ifr_checks
    
    if all(check[1] for check in ifr_checks):
        result['phase'] = 'IFR'
        result['restrictions'] = {
            'solo_cadets': False,
            'first_solo': False
        }
        return result
    
    # If we get here, it's HOLD
    result['phase'] = 'HOLD'
    result['reasons'] = ['Weather below IFR minimums']
    result['restrictions'] = {
        'solo_cadets': False,
        'first_solo': False,
        'note': 'Recover only - no takeoffs'
    }
    
    return result


def _taf_cache_path(icao: str) -> str:
    """Return the cache file path for an ICAO code."""
    return os.path.join(TAF_CACHE_DIR, f"{icao.upper()}.taf")


def _read_taf_cache(icao: str) -> Optional[str]:
    """Read TAF from cache if it exists and is fresh (< 30 min old)."""
    path = _taf_cache_path(icao)
    try:
        stat = os.stat(path)
        age = time.time() - stat.st_mtime
        if age < TAF_CACHE_EXPIRY_SECS:
            with open(path, 'r') as f:
                data = f.read().strip()
                if data:
                    return data
    except (OSError, IOError):
        pass
    return None


def _write_taf_cache(icao: str, taf_data: str) -> None:
    """Write TAF data to cache file."""
    try:
        os.makedirs(TAF_CACHE_DIR, exist_ok=True)
        path = _taf_cache_path(icao)
        with open(path, 'w') as f:
            f.write(taf_data)
    except (OSError, IOError):
        pass  # Cache write failure is non-fatal


def fetch_taf(icao: str, aliases: list = None, use_cache: bool = True) -> Optional[str]:
    """Fetch TAF from aviationweather.gov with caching and fallback sources.
    
    Tries ICAO aliases if primary returns empty.
    Uses file-based cache (30 min expiry) unless use_cache=False.
    Falls back to alternate URL if primary fails.
    """
    codes_to_try = [icao] + (aliases or [])
    
    # Check cache first (for primary code)
    if use_cache:
        for code in codes_to_try:
            cached = _read_taf_cache(code)
            if cached:
                return cached
    
    # Primary + fallback URLs for each code
    for code in codes_to_try:
        urls = [
            f"https://aviationweather.gov/api/data/taf?ids={code}&format=raw",
            f"https://aviationweather.gov/api/data/taf?ids={code}&format=raw&taf=true",
        ]
        
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    data = response.read().decode('utf-8').strip()
                    if data and not data.startswith('No TAF'):
                        # Cache the result
                        if use_cache:
                            _write_taf_cache(code, data)
                        return data
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                continue
    
    return None


def select_runway(metar: METARParser, airfield_data: dict, icao: str) -> Tuple[str, int]:
    """
    Select most likely runway based on wind.
    
    Returns:
        (runway_id, runway_heading)
    """
    if icao not in airfield_data:
        return "Unknown", 0
    
    runways = airfield_data[icao].get('runways', [])
    if not runways:
        return "Unknown", 0
    
    if metar.wind_dir is None or metar.wind_speed == 0:
        # Calm or variable wind - return first runway
        return runways[0]['id'], runways[0]['heading']
    
    # Find runway most aligned with wind (land into wind)
    best_runway = None
    best_heading = 0
    best_diff = 180
    
    for rwy in runways:
        # For landing, we want runway heading close to wind direction
        diff = abs(rwy['heading'] - metar.wind_dir)
        if diff > 180:
            diff = 360 - diff
        
        if diff < best_diff:
            best_diff = diff
            best_runway = rwy['id']
            best_heading = rwy['heading']
    
    return best_runway or "Unknown", best_heading


def _approach_navaid_required(approach: dict) -> Optional[str]:
    """Return the navaid type required for an approach, or None."""
    app_type = approach.get('type', '').upper()
    if 'ILS' in app_type:
        return 'ILS'
    if 'VOR' in app_type:
        return 'VOR'
    if 'TACAN' in app_type:
        return 'TACAN'
    if 'NDB' in app_type:
        return 'NDB'
    if 'RNAV' in app_type or 'RNP' in app_type or 'GPS' in app_type:
        return None  # Satellite-based, no ground navaid required
    return None


def _is_navaid_serviceable(navaid_type: str, notam_impact: dict) -> bool:
    """Check if a navaid type is serviceable given NOTAM impact data."""
    if not notam_impact:
        return True  # No NOTAM data = assume serviceable
    if navaid_type == 'ILS' and not notam_impact.get('ils_available', True):
        return False
    if navaid_type == 'VOR' and not notam_impact.get('vor_available', True):
        return False
    # For TACAN/NDB, check warnings list
    if navaid_type in ('TACAN', 'NDB'):
        for w in notam_impact.get('warnings', []):
            if f'{navaid_type} unserviceable' in w:
                return False
    return True


def check_alternate_suitability(icao: str, taf_string: Optional[str], 
                                airfield_data: dict, oekf_wind_dir: int = None, 
                                oekf_wind_speed: int = None,
                                use_cache: bool = True,
                                notam_impact: dict = None) -> dict:
    """
    Check if alternate airfield is suitable per FOB 18-3.
    
    FOB 18-3e: Alternate must have:
      1. A published IAP suitable for the aircraft type (navaid must be serviceable)
      2. Actual/forecast weather ETA ±1hr (prevailing OR intermittent/TEMPO):
         - Ceiling: max(1000ft, IAP ceiling + 500ft)
         - Visibility: max(3000m, IAP visibility + 1600m)
    
    All TAF periods (BASE, BECMG, TEMPO) are hard checks — FOB says
    "prevailing or intermittently less than VMC" triggers alternate requirement,
    so TEMPO below minimums rejects the alternate.
    """
    result = {
        'suitable': True,
        'runway': None,
        'approach': None,
        'crosswind': None,
        'tailwind': None,
        'reasons': [],
        'warnings': []
    }
    
    if icao not in airfield_data:
        result['suitable'] = False
        result['reasons'].append('Airfield data not available')
        return result
    
    # Fetch TAF if not provided
    if not taf_string:
        taf_string = fetch_taf(icao, use_cache=use_cache)
    
    if not taf_string:
        # No TAF - use OEKF winds as estimate if available
        if oekf_wind_dir is not None and oekf_wind_speed is not None:
            result['warnings'].append('No TAF - using OEKF winds as estimate')
        else:
            result['suitable'] = False
            result['reasons'].append('TAF not available')
            return result
    
    # Parse TAF
    if taf_string:
        taf = TAFParser(taf_string)
        periods = taf.get_all_periods()
    else:
        # Create pseudo-period from OEKF winds
        periods = [('ESTIMATE', {
            'wind_dir': oekf_wind_dir,
            'wind_speed': oekf_wind_speed,
            'visibility_m': 10000,
            'clouds': [],
            'has_cb': False
        })]
    
    # Get airfield info
    runways = airfield_data[icao].get('runways', [])
    approaches = airfield_data[icao].get('approaches', [])
    
    if not runways:
        result['suitable'] = False
        result['reasons'].append('No runway data')
        return result
    
    # FOB 18-3e(1): Must have a published IAP with serviceable navaid
    # Filter approaches to only those with serviceable navaids
    usable_approaches = []
    rejected_approaches = []
    for app in approaches:
        navaid = _approach_navaid_required(app)
        if navaid and not _is_navaid_serviceable(navaid, notam_impact):
            rejected_approaches.append(f"{app.get('type', '?')} RWY {app.get('runway', '?')} ({navaid} U/S)")
        else:
            usable_approaches.append(app)
    
    if not usable_approaches:
        if rejected_approaches:
            result['suitable'] = False
            result['reasons'].append(f"No usable IAP — {', '.join(rejected_approaches)}")
            # Still set runway/approach info for display
            if approaches:
                result['approach'] = approaches[0]
            return result
        elif not approaches:
            # No approaches defined at all — use generic minimums
            result['warnings'].append('No published IAP data — using generic minimums (1000ft/3000m)')
    
    # Check each TAF period — ALL periods are hard checks per FOB 18-3
    # "prevailing or intermittently less than VMC"
    unsuitable_reasons = []
    
    for period_type, period in periods:
        # Select best runway for this period's wind
        wind_dir = period.get('wind_dir')
        wind_speed = period.get('wind_speed', 0)
        wind_gust = period.get('wind_gust')
        effective_wind = wind_gust if wind_gust else wind_speed
        
        if wind_dir is None:
            runway = runways[0]
        else:
            best_diff = 180
            runway = runways[0]
            for rwy in runways:
                diff = abs(rwy['heading'] - wind_dir)
                if diff > 180:
                    diff = 360 - diff
                if diff < best_diff:
                    best_diff = diff
                    runway = rwy
        
        # Calculate wind components
        if wind_dir is not None and effective_wind is not None:
            crosswind, headwind = calculate_wind_components(wind_dir, effective_wind, runway['heading'])
            tailwind = abs(headwind) if headwind < 0 else 0
        else:
            crosswind = effective_wind if effective_wind is not None else 0
            headwind = 0
            tailwind = 0
        
        # Wind limits
        if crosswind > 24:
            unsuitable_reasons.append(f'{period_type}: Crosswind {crosswind:.1f}kt > 24kt')
        if tailwind > 10:
            unsuitable_reasons.append(f'{period_type}: Tailwind {tailwind:.1f}kt > 10kt')
        
        # FOB 18-3e(2): Weather minimums from usable approach
        # Find best usable approach for this runway
        min_vis_m = 3000
        min_ceiling_ft = 1000
        suitable_approach = None
        
        for app in usable_approaches:
            if app.get('runway') == runway['id'] or app.get('runway') == runway.get('reciprocal'):
                suitable_approach = app
                break
        
        if not suitable_approach and usable_approaches:
            suitable_approach = usable_approaches[0]
        
        if suitable_approach:
            app_vis = suitable_approach['minimums'].get('visibility_m', 800)
            app_ceil = suitable_approach['minimums'].get('ceiling_ft', 200)
            min_vis_m = max(3000, app_vis + 1600)
            min_ceiling_ft = max(1000, app_ceil + 500)
        
        # Check visibility
        vis_m = period.get('visibility_m')
        if vis_m and vis_m < min_vis_m:
            unsuitable_reasons.append(f'{period_type}: Vis {vis_m}m < {min_vis_m}m')
        
        # Check ceiling
        for cloud in period.get('clouds', []):
            if cloud['coverage'] in ['BKN', 'OVC']:
                if cloud['height_ft'] < min_ceiling_ft:
                    unsuitable_reasons.append(
                        f'{period_type}: Ceiling {cloud["height_ft"]}ft < {min_ceiling_ft}ft'
                    )
                break
        
        # CB
        if period.get('has_cb'):
            unsuitable_reasons.append(f'{period_type}: CB forecast')
        
        # Store base period data for display
        if period_type == 'BASE' or result['runway'] is None:
            result['runway'] = runway['id']
            result['crosswind'] = round(crosswind, 1)
            result['tailwind'] = round(tailwind, 1)
            result['approach'] = suitable_approach
    
    # ALL periods are hard rejects per FOB 18-3
    # (TEMPO = "intermittently less than VMC")
    if unsuitable_reasons:
        result['suitable'] = False
        result['reasons'] = unsuitable_reasons
    
    return result


def calculate_divert_fuel(icao: str, airfield_data: dict, solo: bool = False, 
                         opposite: bool = False, oekf_metar: METARParser = None, 
                         alt_taf: TAFParser = None) -> Tuple[int, str]:
    """
    Calculate divert fuel with wind adjustments.
    
    Returns:
        (fuel_lbs, explanation)
    """
    if icao not in airfield_data.get('divert_fuel', {}):
        return 0, "No fuel data"
    
    fuel_data = airfield_data['divert_fuel'][icao]
    base_fuel = fuel_data['base_fuel_lbs']
    track_deg = fuel_data.get('track_deg', 0)
    
    # Start with base fuel
    fuel = base_fuel
    adjustments = []
    
    # Get headwind component for the TRACK to alternate
    headwind_kt = 0
    
    # Prefer TAF winds at alternate
    if alt_taf and alt_taf.base_period:
        wind_dir = alt_taf.base_period.get('wind_dir')
        wind_speed = alt_taf.base_period.get('wind_speed', 0)
        wind_gust = alt_taf.base_period.get('wind_gust')
        effective_wind = wind_gust if wind_gust else wind_speed
        
        if wind_dir is not None:
            _, headwind = calculate_wind_components(wind_dir, effective_wind, track_deg)
            headwind_kt = max(0, headwind)  # Only care about headwind
            adjustments.append(f'Alternate TAF wind: {headwind_kt:.0f}kt headwind')
    elif oekf_metar and oekf_metar.wind_dir is not None:
        # Use OEKF winds as estimate
        effective_wind = oekf_metar.get_effective_wind_speed()
        _, headwind = calculate_wind_components(oekf_metar.wind_dir, effective_wind, track_deg)
        headwind_kt = max(0, headwind)
        adjustments.append(f'OEKF wind estimate: {headwind_kt:.0f}kt headwind')
    
    # Apply headwind adjustment: +5% per 10kt headwind
    if headwind_kt > 0:
        headwind_factor = (headwind_kt / 10) * 0.05
        headwind_fuel = int(base_fuel * headwind_factor)
        fuel += headwind_fuel
        adjustments.append(f'+{headwind_fuel} lbs for {headwind_kt:.0f}kt headwind')
    
    # Solo adjustment
    if solo:
        fuel += 100
        adjustments.append('+100 lbs (solo)')
    
    # Opposite side adjustment
    if opposite:
        fuel += 30
        adjustments.append('+30 lbs (opposite side divert)')
    
    explanation = f"{base_fuel} lbs base"
    if adjustments:
        explanation += " | " + " | ".join(adjustments)
    explanation += f" = {fuel} lbs"
    
    return fuel, explanation


def format_output(phase_result: dict, metar: METARParser, runway: str, 
                  alternate_required: bool, checked_alternates: List[dict] = None,
                  best_alternate: dict = None, taf: TAFParser = None, 
                  warnings: List[str] = None, show_checks: bool = False,
                  bird_info: dict = None, sortie_window: dict = None,
                  parse_warnings: List[str] = None, verbose: bool = False) -> str:
    """Format human-readable output."""
    output = []
    
    # Current Zulu time stamp
    from datetime import datetime, timezone
    zulu_now = datetime.now(timezone.utc)
    output.append(f"🕐 {zulu_now.strftime('%d %b %Y %H%MZ')}")
    output.append("")
    
    # Header
    phase_emoji = {
        'UNRESTRICTED': '🟢',
        'RESTRICTED': '🟡',
        'FS VFR': '🟡',
        'VFR': '🟠',
        'IFR': '🔴',
        'HOLD': '⛔',
        'RECALL': '🚨'
    }
    
    emoji = phase_emoji.get(phase_result['phase'], '❓')
    output.append(f"{emoji} KFAA Phase: {phase_result['phase']}")
    output.append("")
    
    # Always show phase checks so users can verify the determination
    if phase_result.get('checks'):
        output.append("✓ Phase Checks:")
        for phase_name, checks in phase_result['checks'].items():
            output.append(f"  {phase_name}:")
            for check_name, passed in checks:
                check_emoji = "✅" if passed else "❌"
                output.append(f"    {check_emoji} {check_name}")
        output.append("")
    
    # Verbose: show all weather inputs for phase determination
    if verbose and phase_result.get('metar_original'):
        orig = phase_result['metar_original']
        cond_final = phase_result['conditions']
        
        output.append("📋 Phase Determination Inputs (METAR only):")
        output.append("  ┌─ METAR observation (used for phase):")
        
        # Visibility
        orig_vis_str = f"{orig['visibility_km']:.1f}km" if orig['visibility_km'] else "N/A"
        if orig.get('cavok'):
            orig_vis_str = "CAVOK (≥10km)"
        output.append(f"  │  Vis: {orig_vis_str}")
        
        # Cloud
        if orig.get('cavok'):
            orig_cloud_str = "CAVOK"
        elif not orig['clouds']:
            orig_cloud_str = "NSC" if 'NSC' in metar.raw.upper() else "SKC"
        else:
            parts = []
            for c in orig['clouds']:
                s = f"{c['coverage']}{c['height_ft']//100:03d}"
                if c.get('type'):
                    s += c['type']
                parts.append(s)
            orig_cloud_str = " ".join(parts)
        output.append(f"  │  Cloud: {orig_cloud_str}")
        
        # Wind
        if orig['wind_dir'] is not None:
            orig_wind_str = f"{orig['wind_dir']:03d}°/{orig['wind_speed']}kt"
        else:
            orig_wind_str = f"VRB/{orig['wind_speed']}kt"
        if orig['wind_gust']:
            orig_wind_str += f" G{orig['wind_gust']}"
        output.append(f"  │  Wind: {orig_wind_str}")
        
        # Weather
        if orig['weather']:
            output.append(f"  │  Weather: {' '.join(orig['weather'])}")
        
        # Temp/QNH
        extras = []
        if orig['temp'] is not None:
            extras.append(f"Temp: {orig['temp']}/{orig['dewpoint']}°C")
        if orig['qnh'] is not None:
            extras.append(f"QNH: Q{orig['qnh']}")
        if extras:
            output.append(f"  │  {' | '.join(extras)}")
        
        output.append("  │")
        
        # TAF forecast (for alternate assessment, NOT phase)
        if phase_result.get('taf_overlay'):
            output.append("  └─ TAF forecast (for alternate assessment only):")
            for factor in phase_result['taf_overlay']:
                output.append(f"       • {factor}")
        else:
            output.append("  └─ TAF: none provided (alternates use OEKF wind estimate)")
        output.append("")
    
    # Conditions
    cond = phase_result['conditions']
    output.append("📊 Conditions (OEKF):")
    
    vis_str = f"{cond['visibility_km']:.1f}km" if cond['visibility_km'] else "N/A"
    
    if cond.get('cavok'):
        cloud_str = "CAVOK"
    elif not cond['clouds']:
        cloud_str = "NSC" if 'NSC' in metar.raw else "SKC"
    else:
        cloud_parts = []
        for c in cond['clouds']:
            cloud_str_part = f"{c['coverage']}{c['height_ft']//100:03d}"
            if c.get('type'):
                cloud_str_part += c['type']
            cloud_parts.append(cloud_str_part)
        cloud_str = " ".join(cloud_parts)
    
    if cond['wind_dir'] is not None:
        wind_str = f"{cond['wind_dir']:03d}°/{cond['wind_speed']}kt"
    else:
        wind_str = f"VRB/{cond['wind_speed']}kt"
    
    if cond['wind_gust']:
        wind_str += f" G{cond['wind_gust']}"
    
    output.append(f"  Vis: {vis_str} | Cloud: {cloud_str}")
    output.append(f"  Wind: {wind_str}")
    output.append(f"  RWY {runway}: ⨯ {cond['crosswind']:.1f}kt | ↑ {cond['headwind']:.1f}kt" + 
                  (f" | ↓ {cond['tailwind']:.1f}kt" if cond['tailwind'] > 0 else ""))
    
    if cond['temp'] is not None:
        output.append(f"  Temp: {cond['temp']}°C")
    
    # TAF forecast (does not affect phase, shown for awareness)
    if phase_result.get('taf_overlay'):
        output.append(f"  📅 TAF forecast (alternate assessment only):")
        for factor in phase_result['taf_overlay']:
            output.append(f"    • {factor}")
    
    output.append("")
    
    # Restrictions
    output.append("👨‍✈️ Restrictions:")
    
    restrictions = phase_result.get('restrictions', {})
    
    solo_ok = restrictions.get('solo_cadets', False)
    solo_note = restrictions.get('solo_note', '')
    first_solo_ok = restrictions.get('first_solo', False)
    
    solo_emoji = "✅" if solo_ok else "❌"
    first_solo_emoji = "✅" if first_solo_ok else "❌"
    
    output.append(f"  Solo cadets: {solo_emoji}" + (f" ({solo_note})" if solo_note else ""))
    output.append(f"  1st Solo: {first_solo_emoji}")
    
    if restrictions.get('note'):
        output.append(f"  Note: {restrictions['note']}")
    
    if phase_result.get('reasons'):
        for reason in phase_result['reasons']:
            output.append(f"  • {reason}")
    
    output.append("")
    
    # Bird-Strike Risk Level (LOP 5-13)
    if bird_info:
        level = bird_info['level']
        bird_emoji = '🟡' if level == 'MODERATE' else '🔴'
        output.append(f"🐦 Bird-Strike Risk: {bird_emoji} {level}")
        if bird_info.get('phase_impact'):
            output.append(f"  ⚠️ {bird_info['phase_impact']}")
        for restriction in bird_info.get('restrictions', []):
            output.append(f"  • {restriction}")
        output.append("")
    
    # Sortie Window Analysis
    if sortie_window and sortie_window.get('applicable'):
        sw = sortie_window
        det_flag = " ⚠️ DETERIORATING" if sw.get('deteriorating') else ""
        output.append(f"📅 Sortie Window: {sw['local_start']}-{sw['local_end']}L — {sw['summary']}{det_flag}")
        output.append("")
    
    # TAF Forecast Trend
    if taf and taf.icao == 'OEKF':
        output.append("📈 Forecast (OEKF TAF):")
        
        # Check for improvement or deterioration
        base_vis = taf.base_period.get('visibility_m', 10000) if taf.base_period else 10000
        base_ceiling = None
        
        if taf.base_period:
            for cloud in taf.base_period.get('clouds', []):
                if cloud['coverage'] in ['BKN', 'OVC']:
                    base_ceiling = cloud['height_ft']
                    break
        
        trend = "STABLE"
        
        # Check BECMG periods
        for becmg in taf.becmg_periods:
            becmg_vis = becmg.get('visibility_m', 10000)
            becmg_ceiling = None
            for cloud in becmg.get('clouds', []):
                if cloud['coverage'] in ['BKN', 'OVC']:
                    becmg_ceiling = cloud['height_ft']
                    break
            
            if becmg_vis is not None and base_vis is not None:
                if becmg_vis < base_vis:
                    trend = "DETERIORATING"
                elif becmg_vis > base_vis:
                    trend = "IMPROVING"
            if becmg_ceiling and base_ceiling:
                if becmg_ceiling < base_ceiling:
                    trend = "DETERIORATING"
                elif becmg_ceiling > base_ceiling:
                    trend = "IMPROVING"
        
        output.append(f"  Trend: {trend}")
        
        if taf.tempo_periods:
            output.append(f"  TEMPO periods: {len(taf.tempo_periods)}")
        
        if taf.base_period and taf.base_period.get('has_cb'):
            output.append("  ⚠️ CB forecast in TAF")
        
        output.append("")
    
    # Alternate
    alt_str = "REQUIRED" if alternate_required else "NOT REQUIRED"
    output.append(f"✈️  Alternate: {alt_str}")
    
    if alternate_required:
        output.append("")
        
        if best_alternate and best_alternate.get('suitable'):
            icao = best_alternate['icao']
            name = best_alternate['name']
            fuel_str = best_alternate.get('fuel_explanation', 'N/A')
            
            output.append(f"  ✅ Selected: {icao} ({name})")
            output.append(f"  Fuel: {fuel_str}")
            
            if best_alternate.get('runway'):
                output.append(f"  RWY {best_alternate['runway']}: " +
                            f"⨯ {best_alternate.get('crosswind', 0):.1f}kt")
            
            if best_alternate.get('approach'):
                app = best_alternate['approach']
                output.append(f"  Approach: {app['type']} " +
                            f"(mins: {app['minimums']['ceiling_ft']}ft / {app['minimums']['visibility_m']}m)")
            
            if best_alternate.get('warnings'):
                for warning in best_alternate['warnings']:
                    output.append(f"  ⚠️ {warning}")
        else:
            output.append("  ❌ No suitable alternate found")
        
        # Show other checked alternates
        if checked_alternates and len(checked_alternates) > 1:
            output.append("")
            output.append("  Other alternates checked:")
            for alt in checked_alternates:
                if alt['icao'] == best_alternate.get('icao'):
                    continue  # Skip the selected one
                
                status = "✅" if alt.get('suitable') else "❌"
                reason = alt.get('reasons', [''])[0] if alt.get('reasons') else ''
                label = reason if reason else 'Suitable'
                output.append(f"    {status} {alt['icao']} - {label}")
                # Show NOTAM warnings for this alternate
                notam_warns = [w for w in alt.get('warnings', []) if w.startswith('NOTAM:')]
                for nw in notam_warns:
                    output.append(f"      ⚠️ {nw}")
    
    output.append("")
    
    # Verbose: show alternate weather inputs
    if verbose and checked_alternates:
        output.append("📋 Alternate Assessment Inputs:")
        for alt in checked_alternates:
            status = "✅" if alt.get('suitable') else "❌"
            output.append(f"  {status} {alt['icao']} ({alt['name']}):")
            
            taf_raw = alt.get('taf_raw')
            if taf_raw:
                # Parse the TAF to show structured conditions
                alt_taf = TAFParser(taf_raw)
                bp = alt_taf.base_period
                if bp:
                    vis_str = f"{bp['visibility_m']}m" if bp.get('visibility_m') else "N/A"
                    wind_str = ""
                    if bp.get('wind_dir') is not None:
                        wind_str = f"{bp['wind_dir']:03d}°/{bp['wind_speed']}kt"
                        if bp.get('wind_gust'):
                            wind_str += f" G{bp['wind_gust']}"
                    elif bp.get('wind_speed') is not None:
                        wind_str = f"VRB/{bp['wind_speed']}kt"
                    
                    cloud_parts = []
                    for c in bp.get('clouds', []):
                        s = f"{c['coverage']}{c['height_ft']//100:03d}"
                        if c.get('type'):
                            s += c['type']
                        cloud_parts.append(s)
                    cloud_str = " ".join(cloud_parts) if cloud_parts else "NSC/SKC"
                    
                    output.append(f"    TAF base: Vis {vis_str} | Cloud: {cloud_str} | Wind: {wind_str}")
                
                # Show BECMG/TEMPO periods
                for becmg in alt_taf.becmg_periods:
                    parts = []
                    if becmg.get('visibility_m'):
                        parts.append(f"vis {becmg['visibility_m']}m")
                    if becmg.get('clouds'):
                        for c in becmg['clouds']:
                            parts.append(f"{c['coverage']}{c['height_ft']//100:03d}")
                    if becmg.get('wind_speed'):
                        parts.append(f"wind {becmg.get('wind_dir', '???')}/{becmg['wind_speed']}kt")
                    if parts:
                        output.append(f"    BECMG: {', '.join(parts)}")
                
                for tempo in alt_taf.tempo_periods:
                    parts = []
                    if tempo.get('visibility_m'):
                        parts.append(f"vis {tempo['visibility_m']}m")
                    if tempo.get('clouds'):
                        for c in tempo['clouds']:
                            parts.append(f"{c['coverage']}{c['height_ft']//100:03d}")
                    if tempo.get('wind_speed'):
                        parts.append(f"wind {tempo.get('wind_dir', '???')}/{tempo['wind_speed']}kt")
                    if parts:
                        output.append(f"    TEMPO: {', '.join(parts)}")
            else:
                output.append(f"    TAF: not available")
            
            if alt.get('runway'):
                xw = f"⨯ {alt.get('crosswind', 0):.1f}kt" if alt.get('crosswind') is not None else ""
                tw = f" ↓ {alt.get('tailwind', 0):.1f}kt" if alt.get('tailwind') and alt['tailwind'] > 0 else ""
                output.append(f"    RWY {alt['runway']}: {xw}{tw}")
            
            if alt.get('approach'):
                app = alt['approach']
                output.append(f"    Approach: {app['type']} (mins: {app['minimums']['ceiling_ft']}ft / {app['minimums']['visibility_m']}m)")
            
            for r in alt.get('reasons', []):
                output.append(f"    ❌ {r}")
            for w in alt.get('warnings', []):
                output.append(f"    ⚠️ {w}")
        output.append("")
    
    # Parse warnings (non-critical)
    if parse_warnings:
        output.append("🔍 Parse Notes:")
        for pw in parse_warnings:
            output.append(f"  {pw}")
        output.append("")
    
    # Warnings
    if warnings:
        output.append("⚠️  Warnings:")
        for warning in warnings:
            output.append(f"  • {warning}")
        output.append("")
    
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description='KFAA Flying Phase Determination')
    parser.add_argument('metar', help='METAR string for OEKF')
    parser.add_argument('taf', nargs='?', help='TAF string for OEKF (optional)')
    parser.add_argument('--rwy', '--runway', dest='runway', help='Runway in use (e.g., 33L)')
    parser.add_argument('--warning', help='Weather warning string')
    parser.add_argument('--notes', nargs='*', help='Operational notes (e.g., "RADAR procedures only" "No medical")')
    parser.add_argument('--bird', choices=['low', 'moderate', 'severe'], default='low',
                        help='Bird-Strike Risk Level (LOP 5-13). Default: low')
    parser.add_argument('--solo', action='store_true', help='Solo cadet (for fuel calculation)')
    parser.add_argument('--opposite', action='store_true', help='Diverting from opposite side')
    parser.add_argument('--checks', action='store_true', help='Show phase condition checks')
    parser.add_argument('--verbose', action='store_true', help='Show all weather inputs for phase and alternate determination')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    parser.add_argument('--no-cache', action='store_true', help='Bypass TAF cache')
    parser.add_argument('--notams', action='store_true', help='Check NOTAMs for alternate airfields')
    parser.add_argument('--sortie-time', dest='sortie_time',
                        help='Sortie time in local (AST) HHmm format, e.g. "1030" for 10:30 local')
    
    args = parser.parse_args()
    
    # Load airfield data
    script_dir = Path(__file__).parent
    data_file = script_dir / 'airfield_data.json'
    
    try:
        with open(data_file) as f:
            airfield_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: airfield_data.json not found at {data_file}", file=sys.stderr)
        sys.exit(1)
    
    # Schema v2 compatibility: flatten 'airfields' dict to top level
    if 'airfields' in airfield_data:
        for icao, af_data in airfield_data['airfields'].items():
            airfield_data[icao] = af_data
        # Normalize runway headings from "149/329" string to individual runway dicts
        for icao in list(airfield_data.get('airfields', {}).keys()):
            af = airfield_data[icao]
            new_runways = []
            for rwy in af.get('runways', []):
                if '/' in str(rwy.get('id', '')):
                    ids = rwy['id'].split('/')
                    hdgs = str(rwy.get('heading', '')).split('/')
                    if len(ids) == 2 and len(hdgs) == 2:
                        new_runways.append({
                            'id': ids[0], 'heading': int(hdgs[0]),
                            'reciprocal': ids[1]
                        })
                        new_runways.append({
                            'id': ids[1], 'heading': int(hdgs[1]),
                            'reciprocal': ids[0]
                        })
                    else:
                        new_runways.append(rwy)
                else:
                    new_runways.append(rwy)
            af['runways'] = new_runways
    # Normalize divert_fuel keys: fuel_lbs -> base_fuel_lbs, bearing -> track_deg
    for icao, fuel in airfield_data.get('divert_fuel', {}).items():
        if 'fuel_lbs' in fuel and 'base_fuel_lbs' not in fuel:
            fuel['base_fuel_lbs'] = fuel.pop('fuel_lbs')
        if 'bearing' in fuel and 'track_deg' not in fuel:
            fuel['track_deg'] = fuel.pop('bearing')
    
    # Parse METAR
    metar = METARParser(args.metar)
    
    # Validate METAR parse
    parse_issues = metar.validate()
    critical_issues = [i for i in parse_issues if i.startswith('❌')]
    
    if critical_issues:
        print("⚠️  METAR PARSE ERRORS:", file=sys.stderr)
        print(f"  Input: {args.metar}", file=sys.stderr)
        for issue in parse_issues:
            print(f"  {issue}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Expected format: OEKF DDHHmmZ dddssKT VVVV [wx] [clouds] TT/TD QPPPP", file=sys.stderr)
        print("Example: OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018", file=sys.stderr)
        sys.exit(1)
    
    # Show non-critical parse warnings
    warning_issues = [i for i in parse_issues if i.startswith('⚠️')]
    if warning_issues:
        for issue in warning_issues:
            print(f"Parse: {issue}", file=sys.stderr)
    
    if metar.icao != 'OEKF':
        print(f"Warning: METAR is for {metar.icao}, expected OEKF", file=sys.stderr)
    
    # Parse TAF if provided
    taf = None
    if args.taf:
        taf = TAFParser(args.taf)
    
    # Determine runway
    if args.runway:
        runway = args.runway.upper()
        # Find heading from data
        runway_heading = 0
        for rwy in airfield_data.get('OEKF', {}).get('runways', []):
            if rwy['id'] == runway:
                runway_heading = rwy['heading']
                break
    else:
        runway, runway_heading = select_runway(metar, airfield_data, 'OEKF')
    
    # Snapshot raw METAR observation (for verbose output)
    metar_original = {
        'visibility_m': metar.visibility_m,
        'visibility_km': metar.visibility_m / 1000 if metar.visibility_m else None,
        'clouds': [dict(c) for c in metar.clouds],
        'wind_dir': metar.wind_dir,
        'wind_speed': metar.wind_speed,
        'wind_gust': metar.wind_gust,
        'cavok': metar.cavok,
        'weather': list(metar.weather),
        'temp': metar.temp,
        'dewpoint': metar.dewpoint,
        'qnh': metar.qnh,
    }
    
    # --- PHASE DETERMINATION: METAR + Warnings + Notes (NOT TAF) ---
    # Phase reflects CURRENT conditions only. TAF is used for alternate assessment.
    phase_result = determine_phase(metar, runway_heading, airfield_data)
    
    # Store original METAR for verbose output
    phase_result['metar_original'] = metar_original
    
    # --- TAF OVERLAY: for alternate assessment and display only ---
    # TAF does NOT affect phase determination.
    taf_overlay_factors = []
    if taf and metar.obs_hour is not None:
        taf_window = taf.get_planning_window(
            metar.obs_hour, metar.obs_minute or 0, window_min=30
        )
        taf_overlay_factors = metar.apply_taf_overlay(taf_window)
        taf_overlay_factors.extend(taf_window.get('factors', []))
    
    # Tag TAF overlay info onto phase_result for display context
    if taf_overlay_factors:
        phase_result['taf_overlay'] = taf_overlay_factors
    
    # Check if alternate required (from METAR conditions + TAF forecast)
    alternate_required = False
    warnings = []
    
    cond = phase_result['conditions']
    
    # Alternate required if below VFR minimums OR if forecast deteriorates
    if cond['visibility_km'] and cond['visibility_km'] < 5:
        alternate_required = True
        warnings.append(f"Visibility below VFR minimums ({cond['visibility_km']}km)")
    
    if cond['ceiling_ft'] and cond['ceiling_ft'] < 1500:
        alternate_required = True
        warnings.append(f"Ceiling below VFR minimums ({cond['ceiling_ft']}ft)")
    
    # Add CB-specific warnings from METAR
    cb_warns = cond.get('cb_warnings', [])
    if cb_warns and phase_result['phase'] != 'RECALL':
        for cw in cb_warns:
            warnings.append(f"⛈️ {cw}")
    
    # Check TAF for CB in any period
    if taf:
        for period_type, period in taf.get_all_periods():
            if period.get('has_cb') and period_type != 'BASE':
                # Already added via check_deterioration for TEMPO, but add specific CB warning
                pass  # Handled below in TAF deterioration check
    
    # Check TAF for deterioration
    if taf:
        deteriorates, det_reason = taf.check_deterioration()
        if deteriorates:
            alternate_required = True
            warnings.append(f"TAF forecast deterioration: {det_reason}")
        
        # Check for CB in TAF
        for period_type, period in taf.get_all_periods():
            if period.get('has_cb'):
                alternate_required = True
                warnings.append(f"CB forecast in TAF ({period_type})")
                break
    
    # Bird-Strike Risk Level (LOP 5-13)
    # UNRESTRICTED and RESTRICTED imply solo cadets — cannot be phased when birds > LOW.
    # FS VFR implies 1st solo cadets — also cannot be phased when birds > LOW.
    # Highest declarable phase during birds > LOW is VFR.
    bird_level = args.bird.upper()
    bird_info = None
    
    if bird_level != 'LOW':
        weather_phase = phase_result['phase']  # Original weather-determined phase
        bird_info = {'level': bird_level, 'restrictions': [], 'phase_impact': None,
                     'weather_phase': weather_phase}
        
        # Cap phase at VFR — solo phases cannot be declared when birds > LOW
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if weather_phase in solo_phases:
            phase_result['phase'] = 'VFR'
            phase_result['restrictions'] = {
                'solo_cadets': False,
                'first_solo': False,
                'solo_note': f'Bird activity {bird_level} — phase capped at VFR'
            }
            bird_info['phase_impact'] = (
                f"Weather supports {weather_phase} — "
                f"capped to VFR (birds {bird_level}, no solo phases)"
            )
        else:
            # VFR/IFR/HOLD/RECALL — phase unchanged, but still no solo
            phase_result['restrictions']['solo_cadets'] = False
            phase_result['restrictions']['first_solo'] = False
        
        if bird_level == 'MODERATE':
            bird_info['restrictions'] = [
                'No formation wing take-offs',
                'No solo cadet take-offs'
            ]
            warnings.append(f'🐦 Bird-Strike Risk: MODERATE — phase capped at VFR, no solo ops')
            
        elif bird_level == 'SEVERE':
            bird_info['restrictions'] = [
                'No further take-offs',
                'Recovery via single aircraft straight-in/instrument approaches only',
                'Consider changing active runway',
                'Divert aircraft as required (fuel permitting)'
            ]
            phase_result['restrictions']['note'] = 'SEVERE BIRDS: No take-offs. Single aircraft straight-in recovery only.'
            warnings.append(f'🐦 Bird-Strike Risk: SEVERE — NO TAKE-OFFS, straight-in recovery only')
    
    # Add weather warning — parse and apply to phase determination
    if args.warning:
        warnings.append(f"Weather warning: {args.warning}")
        warn_upper = args.warning.upper()
        
        # CB/TS in warning → RECALL
        # Use word boundary for TS to avoid false positives (e.g., "gusts" contains "TS")
        if re.search(r'\bCB\b', warn_upper) or 'THUNDERSTORM' in warn_upper or re.search(r'\bTS\b', warn_upper):
            alternate_required = True
            if phase_result['phase'] not in ['RECALL', 'HOLD']:
                phase_result['phase'] = 'RECALL'
                phase_result['reasons'].append('⚠️ Warning: CB/TS activity')
        
        # Parse visibility from warning text
        # Patterns: "visibility 2000 or less", "vis below 3000", "vis 1500m", "vis < 5km"
        warn_vis_m = None
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
                if 'KM' in (vm.group(0) if hasattr(vm, 'group') else ''):
                    val = val * 1000
                # Check if it looks like meters (>= 100) or km (< 100)
                if val < 100:
                    val = val * 1000  # Likely km
                warn_vis_m = val
                break
        
        # Parse wind from warning text
        # Patterns: "wind 35kt", "gusts 40", "wind exceeding 30"
        warn_wind = None
        wind_patterns = [
            r'(?:WIND|GUST)S?\s+(?:EXCEEDING\s+|ABOVE\s+|>?\s*)(\d+)\s*(?:KT|KNOTS?)?',
            r'(\d+)\s*(?:KT|KNOTS?)\s+(?:WIND|GUST)',
        ]
        for wp in wind_patterns:
            wm = re.search(wp, warn_upper)
            if wm:
                warn_wind = int(wm.group(1))
                break
        
        # Dust/sand storms → assume low visibility
        if any(x in warn_upper for x in ['DUST STORM', 'SANDSTORM', 'SAND STORM', 'BLDU', 'BLSA', 'DS', 'SS']):
            if warn_vis_m is None:
                warn_vis_m = 1000  # Assume poor vis with dust/sand storm warning
        
        # Phase hierarchy (most restrictive first): RECALL > HOLD > IFR > VFR > FS VFR > RESTRICTED > UNRESTRICTED
        phase_rank = {'RECALL': 6, 'HOLD': 5, 'IFR': 4, 'VFR': 3, 'FS VFR': 2, 'RESTRICTED': 1, 'UNRESTRICTED': 0}
        current_rank = phase_rank.get(phase_result['phase'], 0)
        
        # Apply warning visibility to phase
        if warn_vis_m is not None:
            if warn_vis_m < 2400:  # Below IFR minimums → HOLD
                new_rank = phase_rank['HOLD']
                if new_rank > current_rank:
                    phase_result['phase'] = 'HOLD'
                    phase_result['reasons'].append(f'⚠️ Warning: visibility {int(warn_vis_m)}m — below IFR minimums')
            elif warn_vis_m < 5000:  # Below VFR → IFR
                new_rank = phase_rank['IFR']
                if new_rank > current_rank:
                    phase_result['phase'] = 'IFR'
                    phase_result['reasons'].append(f'⚠️ Warning: visibility {int(warn_vis_m)}m — IFR conditions')
                    phase_result['restrictions'] = {'solo_cadets': False, 'first_solo': False}
            elif warn_vis_m < 8000:  # Below UNRESTRICTED/RESTRICTED → VFR
                new_rank = phase_rank['VFR']
                if new_rank > current_rank:
                    phase_result['phase'] = 'VFR'
                    phase_result['reasons'].append(f'⚠️ Warning: visibility {int(warn_vis_m)}m — VFR only')
                    phase_result['restrictions'] = {'solo_cadets': False, 'first_solo': False}
        
        # Apply warning wind to phase
        if warn_wind is not None:
            if warn_wind > 35:
                if phase_rank['RECALL'] > current_rank:
                    phase_result['phase'] = 'RECALL'
                    phase_result['reasons'].append(f'⚠️ Warning: wind {warn_wind}kt exceeds 35kt')
            elif warn_wind > 30:
                if phase_rank['HOLD'] > current_rank:
                    phase_result['phase'] = 'HOLD'
                    phase_result['reasons'].append(f'⚠️ Warning: wind {warn_wind}kt exceeds 30kt')
            elif warn_wind > 25:
                new_rank = phase_rank['VFR']
                if new_rank > current_rank:
                    phase_result['phase'] = 'VFR'
                    phase_result['reasons'].append(f'⚠️ Warning: wind {warn_wind}kt — VFR only')
                    phase_result['restrictions'] = {'solo_cadets': False, 'first_solo': False}
    
    # Sortie time window analysis
    sortie_window = None
    if args.sortie_time and taf:
        try:
            st = args.sortie_time.strip()
            if len(st) == 4 and st.isdigit():
                local_hour = int(st[:2])
                local_min = int(st[2:])
                # Saudi Arabia = UTC+3
                utc_hour = (local_hour - 3) % 24
                
                sortie_window = taf.get_sortie_window_conditions(utc_hour)
                if sortie_window.get('applicable'):
                    # Add local time display info
                    win_start_h = (local_hour - 1) % 24
                    win_end_h = (local_hour + 1) % 24
                    sortie_window['local_start'] = f"{win_start_h:02d}{local_min:02d}"
                    sortie_window['local_end'] = f"{win_end_h:02d}{local_min:02d}"
                    sortie_window['sortie_local'] = st
                    
                    # Flag deterioration as warning
                    if sortie_window.get('deteriorating'):
                        warnings.append(f"📅 Conditions expected to deteriorate during sortie window ({st}L)")
                    if sortie_window.get('has_cb'):
                        warnings.append(f"📅 CB forecast during sortie window ({st}L)")
                        alternate_required = True
            else:
                print(f"Warning: Invalid --sortie-time format '{st}', expected HHmm", file=sys.stderr)
        except (ValueError, IndexError):
            print(f"Warning: Could not parse --sortie-time '{args.sortie_time}'", file=sys.stderr)
    
    # NOTAM check (before alternate selection so it can disqualify airfields)
    notam_results = None
    if args.notams:
        try:
            script_dir = Path(__file__).parent
            if str(script_dir) not in sys.path:
                sys.path.insert(0, str(script_dir))
            from notam_checker import (check_notams_for_alternates, format_notam_report,
                                       get_notam_impact_on_alternate)
            alt_icaos = airfield_data.get('alternate_priority', [])
            notam_results = check_notams_for_alternates(alt_icaos, timeout=15)
        except Exception as e:
            print(f"Warning: NOTAM check failed: {e}", file=sys.stderr)
    
    # Find suitable alternates
    checked_alternates = []
    best_alternate = None
    
    if alternate_required:
        priority = airfield_data.get('alternate_priority', [])
        
        for icao in priority:
            # Fetch TAF for alternate (try ICAO aliases if primary empty)
            aliases = airfield_data.get(icao, {}).get('icao_aliases', [])
            use_cache = not args.no_cache
            alt_taf_str = fetch_taf(icao, aliases=aliases, use_cache=use_cache)
            alt_taf = TAFParser(alt_taf_str) if alt_taf_str else None
            
            # Get NOTAM impact for this alternate (if available)
            notam_impact = None
            if notam_results and notam_results.get('status') == 'ok':
                try:
                    notam_impact = get_notam_impact_on_alternate(icao, notam_results)
                except Exception:
                    pass
            
            suitability = check_alternate_suitability(
                icao, alt_taf_str, airfield_data, 
                metar.wind_dir, metar.get_effective_wind_speed(),
                use_cache=use_cache,
                notam_impact=notam_impact
            )
            
            # Apply NOTAM-level disqualifiers (AD closed, all runways closed)
            if notam_impact:
                try:
                    if not notam_impact['suitable']:
                        suitability['suitable'] = False
                        suitability['reasons'] = suitability.get('reasons', [])
                        suitability['reasons'].insert(0, 'NOTAM: Aerodrome closed')
                    
                    if notam_impact.get('closed_runways'):
                        ad_rwys = [r['id'] for r in airfield_data.get(icao, {}).get('runways', [])]
                        closed = notam_impact['closed_runways']
                        closed_ids = set()
                        for cr in closed:
                            for part in cr.split('/'):
                                closed_ids.add(part.strip())
                        all_closed = all(r in closed_ids for r in ad_rwys) if ad_rwys else False
                        
                        if all_closed and ad_rwys:
                            suitability['suitable'] = False
                            suitability['reasons'] = suitability.get('reasons', [])
                            suitability['reasons'].insert(0, f"NOTAM: All runways closed ({', '.join(closed)})")
                        
                        for cr in closed:
                            suitability['warnings'].append(f"NOTAM: RWY {cr} closed")
                    
                    if notam_impact.get('bird_activity'):
                        suitability['warnings'].append('NOTAM: Bird activity reported')
                except Exception:
                    pass
            
            # Calculate fuel
            fuel_lbs, fuel_explanation = calculate_divert_fuel(
                icao, airfield_data, 
                solo=args.solo, 
                opposite=args.opposite,
                oekf_metar=metar,
                alt_taf=alt_taf
            )
            
            alt_result = {
                'icao': icao,
                'name': airfield_data[icao]['name'],
                'suitable': suitability['suitable'],
                'runway': suitability.get('runway'),
                'crosswind': suitability.get('crosswind'),
                'tailwind': suitability.get('tailwind'),
                'approach': suitability.get('approach'),
                'fuel_lbs': fuel_lbs,
                'fuel_explanation': fuel_explanation,
                'reasons': suitability.get('reasons', []),
                'warnings': suitability.get('warnings', []),
                'taf_raw': alt_taf_str,
            }
            
            checked_alternates.append(alt_result)
            
            if suitability['suitable'] and not best_alternate:
                best_alternate = alt_result
    
    # Output
    if args.json:
        json_output = {
            'phase': phase_result['phase'],
            'conditions': phase_result['conditions'],
            'restrictions': phase_result.get('restrictions', {}),
            'reasons': phase_result.get('reasons', []),
            'checks': phase_result.get('checks', {}),
            'runway': runway,
            'alternate_required': alternate_required,
            'best_alternate': best_alternate,
            'checked_alternates': checked_alternates,
            'warnings': warnings,
            'bird_risk_level': bird_level,
            'bird_info': bird_info,
            'sortie_window': sortie_window,
            'notams': notam_results,
            'notes': args.notes or []
        }
        print(json.dumps(json_output, indent=2))
    else:
        output = format_output(
            phase_result, metar, runway, alternate_required,
            checked_alternates=checked_alternates,
            best_alternate=best_alternate,
            taf=taf,
            warnings=warnings if warnings else None,
            show_checks=True,
            bird_info=bird_info,
            sortie_window=sortie_window,
            parse_warnings=warning_issues if warning_issues else None,
            verbose=args.verbose
        )
        # Append NOTAM results if checked
        if notam_results and notam_results.get('status') == 'ok':
            output += "\n" + format_notam_report(notam_results) + "\n"
        elif notam_results and notam_results.get('status') == 'error':
            output += f"\n⚠️  NOTAM Check: {notam_results.get('message', 'Failed')}\n"
        
        # Append operational notes if provided
        if args.notes:
            output += "\n📋 Operational Notes:"
            for note in args.notes:
                output += f"\n  • {note}"
            output += "\n"
        print(output)


if __name__ == '__main__':
    main()
