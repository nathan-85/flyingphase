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
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Tuple, Optional


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
        self.parse()
    
    def parse(self):
        """Parse METAR string."""
        parts = self.raw.split()
        idx = 0
        
        # Remove "METAR" prefix if present
        if parts and parts[0] == 'METAR':
            parts = parts[1:]
        
        # ICAO code (4 letters)
        for i, part in enumerate(parts):
            if len(part) == 4 and part.isalpha() and part.isupper():
                self.icao = part
                idx = i + 1
                break
        
        # Skip date/time (DDHHmmZ)
        if idx < len(parts) and re.match(r'\d{6}Z', parts[idx]):
            idx += 1
        
        # Wind: 28018G25KT or 28018KT or VRB03KT or 00000KT
        # Also handle variable: 280V340
        if idx < len(parts):
            wind_pattern = r'(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?KT'
            match = re.match(wind_pattern, parts[idx])
            if match:
                if match.group(1) == 'VRB':
                    self.wind_dir = None  # Variable wind
                elif match.group(1) == '000':
                    self.wind_dir = 0
                    self.wind_speed = 0
                else:
                    self.wind_dir = int(match.group(1))
                
                if match.group(1) != '000':
                    self.wind_speed = int(match.group(2))
                    if match.group(4):
                        self.wind_gust = int(match.group(4))
                idx += 1
        
        # Variable wind direction: 280V340
        if idx < len(parts):
            var_pattern = r'(\d{3})V(\d{3})'
            match = re.match(var_pattern, parts[idx])
            if match:
                self.wind_variable_from = int(match.group(1))
                self.wind_variable_to = int(match.group(2))
                idx += 1
        
        # Visibility: 9999, 5000, 3000, CAVOK, P6SM
        # Can have weather: "3000 BR" or "2000 FG HZ"
        if idx < len(parts):
            if parts[idx] == 'CAVOK':
                self.cavok = True
                self.visibility_m = 10000
                # CAVOK means no cloud below 5000ft and vis 10km+
                idx += 1
            elif re.match(r'P?\d+SM', parts[idx]):
                # Statute miles (P6SM = greater than 6SM)
                sm_match = re.match(r'P?(\d+)SM', parts[idx])
                sm = int(sm_match.group(1))
                self.visibility_m = int(sm * 1609)  # Convert to meters
                idx += 1
            elif re.match(r'^\d{4}$', parts[idx]):
                vis = int(parts[idx])
                if vis == 9999:
                    self.visibility_m = 10000
                else:
                    self.visibility_m = vis
                idx += 1
        
        # Runway Visual Range: R33L/1200M or R15L/P2000
        while idx < len(parts) and parts[idx].startswith('R'):
            rvr_pattern = r'R(\d{2}[LCR]?)/([PM]?\d{4})'
            match = re.match(rvr_pattern, parts[idx])
            if match:
                self.rvr.append({
                    'runway': match.group(1),
                    'distance_m': match.group(2)
                })
                idx += 1
            else:
                break
        
        # Weather phenomena (BR, FG, HZ, etc.)
        weather_codes = [
            'MI', 'BC', 'PR', 'DR', 'BL', 'SH', 'TS', 'FZ',  # Descriptors
            'DZ', 'RA', 'SN', 'SG', 'IC', 'PL', 'GR', 'GS',  # Precipitation
            'BR', 'FG', 'FU', 'VA', 'DU', 'SA', 'HZ', 'PO',  # Obscuration
            'SQ', 'FC', 'SS', 'DS'  # Other
        ]
        
        while idx < len(parts):
            part_upper = parts[idx].upper()
            # Check if this part contains weather codes
            is_weather = False
            for code in weather_codes:
                if code in part_upper:
                    is_weather = True
                    break
            
            if is_weather:
                self.weather.append(parts[idx])
                idx += 1
            else:
                break
        
        # Clouds: FEW040, SCT020, BKN015, OVC010, NSC, SKC, NCD
        special_cloud_codes = ['NSC', 'SKC', 'NCD', 'CLR', 'CAVOK']
        
        while idx < len(parts):
            part = parts[idx]
            
            # Check for special cloud codes
            if part in special_cloud_codes:
                # These mean no significant cloud
                idx += 1
                continue
            
            cloud_pattern = r'(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?'
            match = re.match(cloud_pattern, part)
            if match:
                coverage = match.group(1)
                height_ft = int(match.group(2)) * 100
                cloud_type = match.group(3) if match.group(3) else None
                
                self.clouds.append({
                    'coverage': coverage,
                    'height_ft': height_ft,
                    'type': cloud_type
                })
                idx += 1
            else:
                break
        
        # Temperature/Dewpoint: 32/18 or M05/M10 (M = minus)
        while idx < len(parts):
            temp_pattern = r'(M?\d{2})/(M?\d{2})'
            match = re.match(temp_pattern, parts[idx])
            if match:
                temp_str = match.group(1)
                self.temp = int(temp_str.replace('M', '-'))
                dew_str = match.group(2)
                self.dewpoint = int(dew_str.replace('M', '-'))
                idx += 1
                break
            idx += 1
        
        # QNH: Q1012 or A2992
        for part in parts:
            qnh_pattern = r'Q(\d{4})'
            match = re.match(qnh_pattern, part)
            if match:
                self.qnh = int(match.group(1))
                break
    
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
        """Check if CB (cumulonimbus) is present."""
        for cloud in self.clouds:
            if cloud.get('type') == 'CB':
                return True
        return False


class TAFParser:
    """Parse TAF strings and extract forecast periods."""
    
    def __init__(self, taf_string: str):
        self.raw = taf_string.strip()
        self.icao = None
        self.base_period = None
        self.becmg_periods = []
        self.tempo_periods = []
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
        
        # Split into base, BECMG, and TEMPO groups
        # Base is everything before first BECMG/TEMPO
        becmg_pattern = r'BECMG \d{4}/\d{4}'
        tempo_pattern = r'TEMPO \d{4}/\d{4}'
        
        # Find all BECMG periods
        for match in re.finditer(becmg_pattern, text):
            start = match.start()
            # Find end (next BECMG/TEMPO or end of string)
            end = len(text)
            
            # Look for next period marker
            next_match = re.search(r'(BECMG|TEMPO) \d{4}/\d{4}', text[start + 10:])
            if next_match:
                end = start + 10 + next_match.start()
            
            period_text = text[start:end].strip()
            self.becmg_periods.append(self._parse_period(period_text))
        
        # Find all TEMPO periods
        for match in re.finditer(tempo_pattern, text):
            start = match.start()
            end = len(text)
            
            next_match = re.search(r'(BECMG|TEMPO) \d{4}/\d{4}', text[start + 10:])
            if next_match:
                end = start + 10 + next_match.start()
            
            period_text = text[start:end].strip()
            self.tempo_periods.append(self._parse_period(period_text))
        
        # Base period is everything before first BECMG/TEMPO
        base_end = len(text)
        first_becmg = re.search(becmg_pattern, text)
        first_tempo = re.search(tempo_pattern, text)
        
        if first_becmg:
            base_end = min(base_end, first_becmg.start())
        if first_tempo:
            base_end = min(base_end, first_tempo.start())
        
        base_text = text[:base_end].strip()
        self.base_period = self._parse_period(base_text)
    
    def _parse_period(self, period_text: str) -> dict:
        """Parse a single TAF period."""
        result = {
            'raw': period_text,
            'wind_dir': None,
            'wind_speed': None,
            'wind_gust': None,
            'visibility_m': None,
            'clouds': [],
            'weather': [],
            'has_cb': False
        }
        
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
        """Get all periods (base + BECMG + TEMPO)."""
        periods = []
        if self.base_period:
            periods.append(('BASE', self.base_period))
        for p in self.becmg_periods:
            periods.append(('BECMG', p))
        for p in self.tempo_periods:
            periods.append(('TEMPO', p))
        return periods
    
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
        'has_cb': metar.has_cb()
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
    
    if metar.has_cb():
        result['phase'] = 'RECALL'
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
    vfr_checks.append(('Total wind ≤ 35kt', effective_wind <= 35))
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
    ifr_checks.append(('Total wind ≤ 35kt', effective_wind <= 35))
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


def fetch_taf(icao: str, aliases: list = None) -> Optional[str]:
    """Fetch TAF from aviationweather.gov. Tries ICAO aliases if primary returns empty."""
    codes_to_try = [icao] + (aliases or [])
    for code in codes_to_try:
        url = f"https://aviationweather.gov/api/data/taf?ids={code}&format=raw"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = response.read().decode('utf-8').strip()
                if data and not data.startswith('No TAF'):
                    return data
        except (urllib.error.URLError, urllib.error.HTTPError):
            pass
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


def check_alternate_suitability(icao: str, taf_string: Optional[str], 
                                airfield_data: dict, oekf_wind_dir: int = None, 
                                oekf_wind_speed: int = None) -> dict:
    """
    Check if alternate airfield is suitable.
    
    Checks ALL TAF periods (base, BECMG, TEMPO) for suitability.
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
        taf_string = fetch_taf(icao)
    
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
    
    # Check each period
    unsuitable_periods = []
    
    for period_type, period in periods:
        # Select best runway for this period's wind
        wind_dir = period.get('wind_dir')
        wind_speed = period.get('wind_speed', 0)
        wind_gust = period.get('wind_gust')
        effective_wind = wind_gust if wind_gust else wind_speed
        
        if wind_dir is None:
            # Use first runway if no wind data
            runway = runways[0]
        else:
            # Find best runway
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
        
        # Check crosswind and tailwind limits
        if crosswind > 24:
            unsuitable_periods.append(f'{period_type}: Crosswind {crosswind:.1f}kt > 24kt')
        
        if tailwind > 10:
            unsuitable_periods.append(f'{period_type}: Tailwind {tailwind:.1f}kt > 10kt')
        
        # Check ceiling and visibility
        # Find approach minimums
        min_vis_m = 3000
        min_ceiling_ft = 1000
        
        suitable_approach = None
        for app in approaches:
            if app['runway'] == runway['id'] or app.get('runway') == runway.get('reciprocal'):
                suitable_approach = app
                break
        
        if suitable_approach:
            app_vis = suitable_approach['minimums'].get('visibility_m', 800)
            app_ceil = suitable_approach['minimums'].get('ceiling_ft', 200)
            min_vis_m = max(3000, app_vis + 1600)
            min_ceiling_ft = max(1000, app_ceil + 500)
        elif approaches:
            # Use first available approach
            suitable_approach = approaches[0]
            app_vis = suitable_approach['minimums'].get('visibility_m', 800)
            app_ceil = suitable_approach['minimums'].get('ceiling_ft', 200)
            min_vis_m = max(3000, app_vis + 1600)
            min_ceiling_ft = max(1000, app_ceil + 500)
        
        # Check visibility
        vis_m = period.get('visibility_m')
        if vis_m and vis_m < min_vis_m:
            unsuitable_periods.append(f'{period_type}: Vis {vis_m}m < {min_vis_m}m')
        
        # Check ceiling
        for cloud in period.get('clouds', []):
            if cloud['coverage'] in ['BKN', 'OVC']:
                if cloud['height_ft'] < min_ceiling_ft:
                    unsuitable_periods.append(
                        f'{period_type}: Ceiling {cloud["height_ft"]}ft < {min_ceiling_ft}ft'
                    )
                break
        
        # Check for CB
        if period.get('has_cb'):
            unsuitable_periods.append(f'{period_type}: CB present')
        
        # Store first period data
        if period_type == 'BASE' or result['runway'] is None:
            result['runway'] = runway['id']
            result['crosswind'] = round(crosswind, 1)
            result['tailwind'] = round(tailwind, 1)
            result['approach'] = suitable_approach
    
    # Determine suitability
    # BECMG periods make it unsuitable
    # TEMPO periods are warnings but may not reject
    
    for issue in unsuitable_periods:
        if issue.startswith('BECMG'):
            result['suitable'] = False
            result['reasons'].append(issue)
        elif issue.startswith('TEMPO'):
            result['warnings'].append(issue)
        else:
            # BASE period issue
            result['suitable'] = False
            result['reasons'].append(issue)
    
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
                  warnings: List[str] = None, show_checks: bool = False) -> str:
    """Format human-readable output."""
    output = []
    
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
    
    # Show phase checks if requested
    if show_checks and phase_result.get('checks'):
        output.append("✓ Phase Checks:")
        for phase_name, checks in phase_result['checks'].items():
            output.append(f"  {phase_name}:")
            for check_name, passed in checks:
                check_emoji = "✅" if passed else "❌"
                output.append(f"    {check_emoji} {check_name}")
        output.append("")
    
    # Conditions
    cond = phase_result['conditions']
    output.append("📊 Conditions (OEKF):")
    
    vis_str = f"{cond['visibility_km']:.1f}km" if cond['visibility_km'] else "N/A"
    
    if cond.get('cavok'):
        cloud_str = "CAVOK"
    elif not cond['clouds']:
        cloud_str = "SKC"
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
            
            if becmg_vis < base_vis or (becmg_ceiling and base_ceiling and becmg_ceiling < base_ceiling):
                trend = "DETERIORATING"
            elif becmg_vis > base_vis or (becmg_ceiling and base_ceiling and becmg_ceiling > base_ceiling):
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
                output.append(f"    {status} {alt['icao']} - {reason if reason else 'Suitable'}")
    
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
    parser.add_argument('--solo', action='store_true', help='Solo cadet (for fuel calculation)')
    parser.add_argument('--opposite', action='store_true', help='Diverting from opposite side')
    parser.add_argument('--checks', action='store_true', help='Show phase condition checks')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    
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
    
    if not metar.icao:
        print("Error: Could not extract ICAO from METAR", file=sys.stderr)
        sys.exit(1)
    
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
    
    # Determine phase
    phase_result = determine_phase(metar, runway_heading, airfield_data)
    
    # Check if alternate required
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
    
    # Add weather warning
    if args.warning:
        warnings.append(f"Weather warning: {args.warning}")
        if 'CB' in args.warning.upper() or '30NM' in args.warning.upper():
            alternate_required = True
            if phase_result['phase'] not in ['RECALL', 'HOLD']:
                # CB within 30NM triggers RECALL
                phase_result['phase'] = 'RECALL'
                phase_result['reasons'].append('CB within 30NM')
    
    # Find suitable alternates
    checked_alternates = []
    best_alternate = None
    
    if alternate_required:
        priority = airfield_data.get('alternate_priority', [])
        
        for icao in priority:
            # Fetch TAF for alternate (try ICAO aliases if primary empty)
            aliases = airfield_data.get(icao, {}).get('icao_aliases', [])
            alt_taf_str = fetch_taf(icao, aliases=aliases)
            alt_taf = TAFParser(alt_taf_str) if alt_taf_str else None
            
            suitability = check_alternate_suitability(
                icao, alt_taf_str, airfield_data, 
                metar.wind_dir, metar.get_effective_wind_speed()
            )
            
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
                'warnings': suitability.get('warnings', [])
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
            'checks': phase_result.get('checks', {}) if args.checks else {},
            'runway': runway,
            'alternate_required': alternate_required,
            'best_alternate': best_alternate,
            'checked_alternates': checked_alternates,
            'warnings': warnings
        }
        print(json.dumps(json_output, indent=2))
    else:
        output = format_output(
            phase_result, metar, runway, alternate_required,
            checked_alternates=checked_alternates,
            best_alternate=best_alternate,
            taf=taf,
            warnings=warnings if warnings else None,
            show_checks=args.checks
        )
        print(output)


if __name__ == '__main__':
    main()
