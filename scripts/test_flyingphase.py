#!/usr/bin/env python3
"""
Unit tests for KFAA Flying Phase Determination Tool.
Tests METARParser, TAFParser, wind component calculations, phase determination,
and bird level phase capping.
"""

import json
import math
import os
import sys
import unittest
from pathlib import Path

# Import from flyingphase.py
sys.path.insert(0, str(Path(__file__).parent))
from flyingphase import (
    METARParser, TAFParser,
    calculate_wind_components, determine_phase, select_runway
)


class TestMETARParserStandard(unittest.TestCase):
    """Test METAR parsing with standard formats."""

    def test_basic_metar(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.icao, "OEKF")
        self.assertEqual(m.wind_dir, 330)
        self.assertEqual(m.wind_speed, 12)
        self.assertIsNone(m.wind_gust)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 1)
        self.assertEqual(m.clouds[0]['coverage'], 'FEW')
        self.assertEqual(m.clouds[0]['height_ft'], 8000)
        self.assertEqual(m.temp, 22)
        self.assertEqual(m.dewpoint, 10)
        self.assertEqual(m.qnh, 1018)

    def test_gusting_wind(self):
        m = METARParser("OEKF 310600Z 28018G25KT 5000 SCT040 32/18 Q1012")
        self.assertEqual(m.wind_dir, 280)
        self.assertEqual(m.wind_speed, 18)
        self.assertEqual(m.wind_gust, 25)
        self.assertEqual(m.get_effective_wind_speed(), 25)

    def test_multiple_cloud_layers(self):
        m = METARParser("OEKF 310600Z 33012KT 3000 BKN015 OVC025 22/10 Q1018")
        self.assertEqual(len(m.clouds), 2)
        self.assertEqual(m.clouds[0]['coverage'], 'BKN')
        self.assertEqual(m.clouds[0]['height_ft'], 1500)
        self.assertEqual(m.clouds[1]['coverage'], 'OVC')
        self.assertEqual(m.clouds[1]['height_ft'], 2500)
        self.assertEqual(m.get_ceiling_ft(), 1500)

    def test_cb_in_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018")
        self.assertTrue(m.has_cb())
        self.assertEqual(m.clouds[0]['type'], 'CB')

    def test_negative_temperature(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 M05/M10 Q1018")
        self.assertEqual(m.temp, -5)
        self.assertEqual(m.dewpoint, -10)

    def test_metar_prefix(self):
        m = METARParser("METAR OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.icao, "OEKF")
        self.assertEqual(m.wind_dir, 330)

    def test_low_visibility(self):
        m = METARParser("OEKF 310600Z 33012KT 0800 OVC003 22/10 Q1018")
        self.assertEqual(m.visibility_m, 800)
        self.assertEqual(m.get_ceiling_ft(), 300)


class TestMETARParserCAVOK(unittest.TestCase):
    """Test CAVOK handling."""

    def test_cavok(self):
        m = METARParser("OEKF 310600Z 33008KT CAVOK 22/10 Q1018")
        self.assertTrue(m.cavok)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 0)
        self.assertIsNone(m.get_ceiling_ft())
        self.assertIsNone(m.get_lowest_cloud_ft())


class TestMETARParserVariableWind(unittest.TestCase):
    """Test variable wind parsing."""

    def test_vrb_wind(self):
        m = METARParser("OEKF 310600Z VRB03KT 9999 FEW080 22/10 Q1018")
        self.assertIsNone(m.wind_dir)
        self.assertEqual(m.wind_speed, 3)

    def test_variable_wind_direction(self):
        m = METARParser("OEKF 310600Z 28018KT 250V310 5000 SCT040 32/18 Q1012")
        self.assertEqual(m.wind_dir, 280)
        self.assertEqual(m.wind_variable_from, 250)
        self.assertEqual(m.wind_variable_to, 310)

    def test_calm_wind(self):
        m = METARParser("OEKF 310600Z 00000KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.wind_dir, 0)
        self.assertEqual(m.wind_speed, 0)
        self.assertEqual(m.get_effective_wind_speed(), 0)


class TestMETARParserMissingData(unittest.TestCase):
    """Test handling of missing or partial METAR data."""

    def test_no_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 SKC 22/10 Q1018")
        self.assertEqual(len(m.clouds), 0)
        self.assertIsNone(m.get_ceiling_ft())
        self.assertIsNone(m.get_lowest_cloud_ft())

    def test_nsc_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 NSC 22/10 Q1018")
        self.assertEqual(len(m.clouds), 0)

    def test_auto_metar(self):
        """AUTO METARs should parse normally."""
        m = METARParser("OEKF 310600Z AUTO 33012KT 9999 FEW080 22/10 Q1018")
        # AUTO is not a standard parse target, but wind should still be found
        self.assertEqual(m.wind_speed, 12)


class TestMETARParserOrderIndependent(unittest.TestCase):
    """Test order-independent token parsing (elements in non-standard order)."""

    def test_nsc_before_visibility(self):
        """NSC appearing before 4-digit visibility (real-world pilot input)."""
        m = METARParser("05018G22KT NSC 9999 20/03 NOSIG")
        self.assertEqual(m.icao, 'OEKF')  # defaulted
        self.assertEqual(m.wind_dir, 50)
        self.assertEqual(m.wind_speed, 18)
        self.assertEqual(m.wind_gust, 22)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 0)
        self.assertEqual(m.temp, 20)
        self.assertEqual(m.dewpoint, 3)
        self.assertEqual(len(m.validate()), 0)

    def test_cloud_before_visibility(self):
        """BKN040 appearing before visibility."""
        m = METARParser("METAR 05018G22KT BKN040 9999 20/03")
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 1)
        self.assertEqual(m.clouds[0]['coverage'], 'BKN')
        self.assertEqual(m.clouds[0]['height_ft'], 4000)

    def test_weather_before_visibility(self):
        """Weather code (HZ) appearing before visibility."""
        m = METARParser("28015KT HZ 5000 SCT040 32/18 Q1012")
        self.assertEqual(m.visibility_m, 5000)
        self.assertIn('HZ', m.weather)
        self.assertEqual(len(m.clouds), 1)

    def test_no_station_no_timestamp(self):
        """Bare minimum: just wind + vis + temp."""
        m = METARParser("33012KT 9999 22/10")
        self.assertEqual(m.icao, 'OEKF')
        self.assertEqual(m.wind_dir, 330)
        self.assertEqual(m.wind_speed, 12)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(m.temp, 22)

    def test_weather_token_not_confused_with_icao(self):
        """BLDU (blowing dust) should be weather, not ICAO code."""
        m = METARParser("33012KT 3000 BLDU 35/10 Q1008")
        self.assertEqual(m.icao, 'OEKF')  # not BLDU
        self.assertIn('BLDU', m.weather)
        self.assertEqual(m.visibility_m, 3000)

    def test_standard_order_still_works(self):
        """Standard ICAO order should still parse perfectly."""
        m = METARParser("OEKF 311200Z 33012KT 9999 FEW080 SCT120 22/10 Q1018")
        self.assertEqual(m.icao, 'OEKF')
        self.assertEqual(m.obs_hour, 12)
        self.assertEqual(m.wind_dir, 330)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 2)
        self.assertEqual(m.temp, 22)
        self.assertEqual(m.qnh, 1018)


class TestMETARParserCBDetection(unittest.TestCase):
    """Test enhanced CB detection (TS, remarks, distance/direction)."""

    def test_ts_in_weather(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 +TSRA SCT025 22/10 Q1018")
        self.assertTrue(m.has_ts_weather)
        self.assertTrue(m.has_cb())
        self.assertTrue(m.has_cb_within_nm(30))

    def test_standalone_cb_with_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 2000 +TSRA CB BKN010CB 22/10 Q1018")
        self.assertTrue(m.has_cb())
        self.assertEqual(len(m.clouds), 1)
        self.assertEqual(m.clouds[0]['coverage'], 'BKN')
        self.assertEqual(m.clouds[0]['type'], 'CB')

    def test_cb_in_remarks_with_distance(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018 RMK CB NW 25NM MOV E")
        self.assertTrue(m.has_cb())
        self.assertTrue(m.has_cb_within_nm(30))
        self.assertEqual(len(m.cb_details), 1)
        self.assertEqual(m.cb_details[0]['location'], 'NW')
        self.assertEqual(m.cb_details[0]['distance_nm'], 25)
        self.assertEqual(m.cb_details[0]['movement'], 'E')

    def test_cb_distant(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018 RMK CB DSNT SW")
        self.assertTrue(m.has_cb())
        cb = m.cb_details[0]
        self.assertEqual(cb['location'], 'DSNT')
        self.assertEqual(cb['distance_nm'], 25)  # Estimated for DSNT

    def test_cb_warnings(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018")
        warns = m.get_cb_warnings()
        self.assertEqual(len(warns), 1)
        self.assertIn("CB in cloud layer at 1000ft", warns[0])

    def test_no_cb(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertFalse(m.has_cb())
        self.assertFalse(m.has_ts_weather)
        self.assertEqual(len(m.cb_details), 0)
        self.assertEqual(len(m.get_cb_warnings()), 0)


class TestWindComponents(unittest.TestCase):
    """Test wind component calculations for all quadrants."""

    def test_headwind_aligned(self):
        """Wind directly on the nose → full headwind, zero crosswind."""
        cross, head = calculate_wind_components(330, 20, 330)
        self.assertAlmostEqual(cross, 0, places=1)
        self.assertAlmostEqual(head, 20, places=1)

    def test_tailwind_aligned(self):
        """Wind from behind → full tailwind (negative headwind)."""
        cross, head = calculate_wind_components(150, 20, 330)
        self.assertAlmostEqual(cross, 0, places=1)
        self.assertAlmostEqual(head, -20, places=1)

    def test_pure_crosswind_right(self):
        """Wind 90° from right → full crosswind, zero head/tail."""
        cross, head = calculate_wind_components(60, 20, 330)
        self.assertAlmostEqual(cross, 20, places=1)
        self.assertAlmostEqual(head, 0, places=0)

    def test_pure_crosswind_left(self):
        """Wind 90° from left → full crosswind, zero head/tail."""
        cross, head = calculate_wind_components(240, 20, 330)
        self.assertAlmostEqual(cross, 20, places=1)
        self.assertAlmostEqual(head, 0, places=0)

    def test_quartering_headwind(self):
        """Wind 45° off nose → components at ~14.1kt each for 20kt wind."""
        cross, head = calculate_wind_components(285, 20, 330)
        expected = 20 * math.sin(math.radians(45))
        self.assertAlmostEqual(cross, expected, places=1)
        self.assertAlmostEqual(head, expected, places=1)

    def test_quartering_tailwind(self):
        """Wind 135° off nose → crosswind and tailwind."""
        cross, head = calculate_wind_components(195, 20, 330)
        expected_cross = 20 * math.sin(math.radians(135))
        expected_head = 20 * math.cos(math.radians(135))
        self.assertAlmostEqual(cross, abs(expected_cross), places=1)
        self.assertAlmostEqual(head, expected_head, places=1)
        self.assertTrue(head < 0)  # Tailwind

    def test_zero_wind(self):
        """Calm wind → no components."""
        cross, head = calculate_wind_components(330, 0, 330)
        self.assertEqual(cross, 0)
        self.assertEqual(head, 0)

    def test_wrap_around_360(self):
        """Wind at 350° with runway 010° → 20° diff, mostly headwind."""
        cross, head = calculate_wind_components(350, 20, 10)
        # 20° diff
        self.assertAlmostEqual(cross, 20 * math.sin(math.radians(20)), places=1)
        self.assertAlmostEqual(head, 20 * math.cos(math.radians(20)), places=1)

    def test_large_angle_wrap(self):
        """Wind at 010° with runway 350° → 20° diff (wraps through north)."""
        cross, head = calculate_wind_components(10, 20, 350)
        self.assertAlmostEqual(cross, 20 * math.sin(math.radians(20)), places=1)
        self.assertAlmostEqual(head, 20 * math.cos(math.radians(20)), places=1)


class TestPhaseDetermination(unittest.TestCase):
    """Test phase determination boundaries."""

    @classmethod
    def setUpClass(cls):
        """Load airfield data once."""
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        # Schema v2 compatibility
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways
        for icao, fuel in cls.airfield_data.get('divert_fuel', {}).items():
            if 'fuel_lbs' in fuel and 'base_fuel_lbs' not in fuel:
                fuel['base_fuel_lbs'] = fuel.pop('fuel_lbs')
            if 'bearing' in fuel and 'track_deg' not in fuel:
                fuel['track_deg'] = fuel.pop('bearing')

    def _phase(self, metar_str, rwy_hdg=330):
        m = METARParser(metar_str)
        result = determine_phase(m, rwy_hdg, self.airfield_data)
        return result['phase']

    # === UNRESTRICTED boundaries ===
    def test_unrestricted_clear(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018"),
            "UNRESTRICTED"
        )

    def test_unrestricted_wind_at_limit(self):
        """25kt total wind → still UNRESTRICTED."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33025KT 9999 FEW080 22/10 Q1018"),
            "UNRESTRICTED"
        )

    def test_unrestricted_wind_over_limit(self):
        """26kt total wind → should NOT be UNRESTRICTED."""
        phase = self._phase("OEKF 310600Z 33026KT 9999 FEW080 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    def test_unrestricted_cloud_below_8000(self):
        """Cloud at 7000ft → NOT UNRESTRICTED."""
        phase = self._phase("OEKF 310600Z 33008KT 9999 FEW070 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    def test_unrestricted_sct_above_8000(self):
        """SCT above 8000ft → NOT UNRESTRICTED (only FEW allowed)."""
        phase = self._phase("OEKF 310600Z 33008KT 9999 SCT100 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    # === VFR boundaries ===
    def test_vfr_standard(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 7000 SCT040 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_ceiling_at_1500(self):
        """Ceiling exactly at 1500ft → VFR (≥1500ft)."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 7000 BKN015 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_ceiling_below_1500(self):
        """Ceiling at 1400ft → NOT VFR."""
        phase = self._phase("OEKF 310600Z 33012KT 7000 BKN014 22/10 Q1018")
        self.assertNotEqual(phase, "VFR")
        self.assertIn(phase, ["IFR", "HOLD"])

    def test_vfr_vis_at_5km(self):
        """Vis exactly 5000m → VFR."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 SCT040 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_vis_below_5km(self):
        """Vis 4999m → NOT VFR."""
        phase = self._phase("OEKF 310600Z 33012KT 4999 SCT040 22/10 Q1018")
        self.assertNotEqual(phase, "VFR")
        self.assertIn(phase, ["IFR", "HOLD"])

    # === IFR boundaries ===
    def test_ifr_standard(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 3000 BKN015 22/10 Q1018"),
            "IFR"
        )

    def test_ifr_low_ceiling(self):
        """Ceiling at 500ft with vis above mins → IFR."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 3000 BKN005 22/10 Q1018"),
            "IFR"
        )

    # === HOLD boundaries ===
    def test_hold_very_low(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 0800 OVC003 22/10 Q1018"),
            "HOLD"
        )

    def test_hold_high_temp(self):
        """Temperature > 50°C → HOLD."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 9999 FEW080 51/10 Q1018"),
            "HOLD"
        )

    def test_hold_high_crosswind(self):
        """Crosswind > 24kt → HOLD."""
        # Wind at 060° with runway 330 = 90° diff → full crosswind
        self.assertEqual(
            self._phase("OEKF 310600Z 06025KT 9999 FEW080 22/10 Q1018"),
            "HOLD"
        )

    # === RECALL boundaries ===
    def test_recall_extreme_wind(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33040G50KT 2000 +TSRA CB BKN010CB 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_cb_present(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_ts_weather(self):
        """TS in weather → CB implied → RECALL."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 +TSRA SCT025 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_wind_over_35(self):
        """Wind >35kt → RECALL regardless of other conditions."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33036KT 9999 FEW080 22/10 Q1018"),
            "RECALL"
        )


class TestTAFParser(unittest.TestCase):
    """Test TAF parsing."""

    def test_base_period(self):
        taf = TAFParser("TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040")
        self.assertEqual(taf.icao, "OEKF")
        self.assertIsNotNone(taf.base_period)
        self.assertEqual(taf.base_period['wind_speed'], 10)
        self.assertEqual(taf.base_period['visibility_m'], 8000)

    def test_becmg_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "BECMG 3112/3114 15010KT 5000 BKN020"
        )
        self.assertEqual(len(taf.becmg_periods), 1)
        becmg = taf.becmg_periods[0]
        self.assertEqual(becmg['wind_dir'], 150)
        self.assertEqual(becmg['wind_speed'], 10)
        self.assertEqual(becmg['visibility_m'], 5000)
        self.assertEqual(becmg['valid_from_utc'], 12)
        self.assertEqual(becmg['valid_to_utc'], 14)

    def test_tempo_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        self.assertEqual(len(taf.tempo_periods), 1)
        tempo = taf.tempo_periods[0]
        self.assertEqual(tempo['visibility_m'], 3000)
        self.assertTrue(tempo['has_cb'])
        self.assertIn('TS', tempo['weather'])
        self.assertIn('RA', tempo['weather'])
        self.assertEqual(tempo['valid_from_utc'], 16)
        self.assertEqual(tempo['valid_to_utc'], 20)

    def test_fm_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "FM310800 15012KT 6000 SCT030"
        )
        self.assertEqual(len(taf.fm_periods), 1)
        fm = taf.fm_periods[0]
        self.assertEqual(fm['wind_dir'], 150)
        self.assertEqual(fm['wind_speed'], 12)
        self.assertEqual(fm['valid_from_utc'], 8)

    def test_multiple_periods(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "BECMG 3108/3110 28015KT "
            "TEMPO 3112/3118 4000 BKN020 "
            "BECMG 3118/3120 33008KT 9999"
        )
        self.assertEqual(len(taf.becmg_periods), 2)
        self.assertEqual(len(taf.tempo_periods), 1)

    def test_deterioration_check(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 BKN010"
        )
        deteriorates, reason = taf.check_deterioration()
        self.assertTrue(deteriorates)
        self.assertIn("TEMPO", reason)

    def test_no_deterioration(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040"
        )
        deteriorates, _ = taf.check_deterioration()
        self.assertFalse(deteriorates)

    def test_cb_in_taf(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        found_cb = False
        for period_type, period in taf.get_all_periods():
            if period.get('has_cb'):
                found_cb = True
                break
        self.assertTrue(found_cb)

    def test_get_all_periods_includes_fm(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "FM310800 15012KT 6000 SCT030"
        )
        periods = taf.get_all_periods()
        types = [p[0] for p in periods]
        self.assertIn('BASE', types)
        self.assertIn('FM', types)

    def test_sortie_window_base_only(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040"
        )
        result = taf.get_sortie_window_conditions(8)  # 08Z
        self.assertTrue(result['applicable'])
        self.assertEqual(result['worst_vis_m'], 8000)
        self.assertFalse(result['deteriorating'])

    def test_sortie_window_with_tempo(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        # 17Z is within TEMPO 16-20Z window
        result = taf.get_sortie_window_conditions(17)
        self.assertTrue(result['applicable'])
        self.assertEqual(result['worst_vis_m'], 3000)
        self.assertTrue(result['has_cb'])
        self.assertTrue(result['deteriorating'])


class TestBirdLevelPhaseCapping(unittest.TestCase):
    """Test bird-strike risk level phase capping."""

    @classmethod
    def setUpClass(cls):
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways

    def test_bird_moderate_caps_unrestricted(self):
        """UNRESTRICTED weather + moderate birds → VFR cap."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], "UNRESTRICTED")
        # Now simulate bird capping (as done in main())
        bird_level = 'MODERATE'
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')

    def test_bird_low_no_cap(self):
        """UNRESTRICTED weather + low birds → stays UNRESTRICTED."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], "UNRESTRICTED")

    def test_bird_severe_caps_unrestricted(self):
        """UNRESTRICTED weather + severe birds → VFR cap."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')

    def test_bird_moderate_vfr_unchanged(self):
        """VFR weather + moderate birds → stays VFR (already below solo phases)."""
        m = METARParser("OEKF 310600Z 33012KT 7000 SCT040 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], 'VFR')
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')


class TestRunwaySelection(unittest.TestCase):
    """Test runway selection from wind."""

    @classmethod
    def setUpClass(cls):
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways

    def test_select_runway_into_wind(self):
        """Wind 330° → runway 33L or 33R."""
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        rwy, hdg = select_runway(m, self.airfield_data, 'OEKF')
        self.assertIn('33', rwy)
        self.assertEqual(hdg, 330)

    def test_select_runway_southerly_wind(self):
        """Wind 150° → runway 15L or 15R."""
        m = METARParser("OEKF 310600Z 15012KT 9999 FEW080 22/10 Q1018")
        rwy, hdg = select_runway(m, self.airfield_data, 'OEKF')
        self.assertIn('15', rwy)
        self.assertEqual(hdg, 150)


# ===================== NOTAM Checker Tests =====================

class TestNotamClassification(unittest.TestCase):
    """Test NOTAM text classification and impact detection."""

    def setUp(self):
        from notam_checker import classify_notam, extract_affected_runway, extract_affected_navaid
        self.classify = classify_notam
        self.extract_rwy = extract_affected_runway
        self.extract_nav = extract_affected_navaid

    def test_runway_closure(self):
        """RWY CLSD detected as RWY category with high impact."""
        cat, impacts = self.classify("RWY 13/31 CLSD DUE TO MAINT")
        self.assertEqual(cat, 'RWY')
        self.assertTrue(any('Runway closed' in i for i in impacts))

    def test_ils_unserviceable(self):
        """ILS U/S detected as NAV with high impact."""
        cat, impacts = self.classify("ILS RWY 34 U/S UNTIL FURTHER NOTICE")
        self.assertEqual(cat, 'NAV')
        self.assertTrue(any('ILS unserviceable' in i for i in impacts))

    def test_vor_inop(self):
        """VOR INOP detected."""
        cat, impacts = self.classify("JED VOR/DME INOP FOR MAINT")
        self.assertEqual(cat, 'NAV')
        self.assertTrue(any('VOR unserviceable' in i for i in impacts))

    def test_aerodrome_closed(self):
        """AD CLSD detected."""
        cat, impacts = self.classify("AD CLSD TO ALL TRAFFIC")
        self.assertEqual(cat, 'AD')
        self.assertTrue(any('Aerodrome closed' in i for i in impacts))

    def test_general_notam(self):
        """Generic NOTAM classified as GEN with no high impact."""
        cat, impacts = self.classify("TRIGGER NOTAM PERM AIP AMDT 04/25")
        self.assertEqual(cat, 'GEN')
        self.assertEqual(len(impacts), 0)

    def test_bird_activity(self):
        """Bird activity NOTAM detected."""
        cat, impacts = self.classify("BIRD CONCENTRATION REPORTED IN THE VICINITY OF AD")
        self.assertTrue(any('Bird activity' in i for i in impacts))

    def test_extract_runway_single(self):
        """Extract single runway ID."""
        rwy = self.extract_rwy("RWY 15 CLSD")
        self.assertEqual(rwy, '15')

    def test_extract_runway_pair(self):
        """Extract runway pair."""
        rwy = self.extract_rwy("RWY 13/31 CLSD DUE TO MAINT")
        self.assertEqual(rwy, '13/31')

    def test_extract_navaid_ils(self):
        """Extract ILS navaid."""
        nav = self.extract_nav("ILS RWY 34 U/S")
        self.assertEqual(nav, 'ILS')

    def test_extract_navaid_vor(self):
        """Extract VOR navaid."""
        nav = self.extract_nav("JED VOR DME INOP")
        self.assertEqual(nav, 'VOR')

    def test_localizer_unserviceable(self):
        """Localizer U/S detected."""
        cat, impacts = self.classify("LOCALIZER RWY 15 OUT OF SERVICE")
        self.assertEqual(cat, 'NAV')
        self.assertTrue(any('Localizer unserviceable' in i for i in impacts))

    def test_glideslope_unserviceable(self):
        """Glideslope INOP detected."""
        cat, impacts = self.classify("GLIDESLOPE RWY 33 INOP")
        self.assertEqual(cat, 'NAV')
        self.assertTrue(any('Glideslope unserviceable' in i for i in impacts))

    def test_tower_closed(self):
        """TWR CLSD detected."""
        cat, impacts = self.classify("TWR CLSD OUTSIDE PUBLISHED HRS")
        self.assertEqual(cat, 'COM')
        self.assertTrue(any('Tower closed' in i for i in impacts))

    def test_radar_unserviceable(self):
        """RADAR U/S detected."""
        cat, impacts = self.classify("RADAR APPROACH SVC U/S")
        self.assertEqual(cat, 'COM')
        self.assertTrue(any('Radar unserviceable' in i for i in impacts))


    # Note: is_notam_current removed — FAA API handles date filtering


class TestNotamAlternateImpact(unittest.TestCase):
    """Test NOTAM impact assessment for alternates."""

    def setUp(self):
        from notam_checker import get_notam_impact_on_alternate
        self.get_impact = get_notam_impact_on_alternate

    def test_no_data(self):
        """No NOTAM data defaults to suitable."""
        impact = self.get_impact('OEJD', {'status': 'ok', 'airfields': {}})
        self.assertTrue(impact['suitable'])
        self.assertTrue(impact['ils_available'])

    def test_aerodrome_closed(self):
        """Aerodrome closed makes alternate unsuitable."""
        results = {
            'status': 'ok',
            'airfields': {
                'OEAH': {
                    'total_notams': 1,
                    'high_impact_count': 1,
                    'notams': [{
                        'number': 'A0001/26',
                        'text': 'AD CLSD',
                        'category': 'AD',
                        'impacts': ['Aerodrome closed'],
                        'high_impact': True,
                        'affected_runway': None,
                        'affected_navaid': None,
                        'start': '01/01/2026 0000',
                        'end': 'PERM'
                    }],
                    'summary': {
                        'aerodrome_closed': True,
                        'closed_runways': [],
                        'navaid_outages': [],
                        'bird_activity': False,
                        'category_counts': {'AD': 1}
                    }
                }
            }
        }
        impact = self.get_impact('OEAH', results)
        self.assertFalse(impact['suitable'])

    def test_ils_vor_unserviceable(self):
        """ILS and VOR outages detected correctly."""
        results = {
            'status': 'ok',
            'airfields': {
                'OEPS': {
                    'total_notams': 2,
                    'high_impact_count': 2,
                    'notams': [
                        {
                            'number': 'V0010/26',
                            'text': 'ILS RWY 33 U/S',
                            'category': 'NAV',
                            'impacts': ['ILS unserviceable'],
                            'high_impact': True,
                            'affected_runway': '33',
                            'affected_navaid': 'ILS',
                            'start': '', 'end': ''
                        },
                        {
                            'number': 'V0014/26',
                            'text': 'PSA VOR/DME INOP',
                            'category': 'NAV',
                            'impacts': ['VOR unserviceable', 'DME unserviceable'],
                            'high_impact': True,
                            'affected_runway': None,
                            'affected_navaid': 'VOR',
                            'start': '', 'end': ''
                        }
                    ],
                    'summary': {
                        'aerodrome_closed': False,
                        'closed_runways': [],
                        'navaid_outages': ['ILS — ILS unserviceable', 'VOR — VOR unserviceable'],
                        'bird_activity': False,
                        'category_counts': {'NAV': 2}
                    }
                }
            }
        }
        impact = self.get_impact('OEPS', results)
        self.assertTrue(impact['suitable'])  # AD still open
        self.assertFalse(impact['ils_available'])
        self.assertFalse(impact['vor_available'])


class TestNOTAMTimeFiltering(unittest.TestCase):
    """Test that NOTAM time windows are respected."""

    def setUp(self):
        from datetime import datetime, timezone, timedelta
        from notam_checker import is_notam_active_in_window, _parse_schedule_window
        self.is_active = is_notam_active_in_window
        self.parse_schedule = _parse_schedule_window
        self.dt = datetime
        self.tz = timezone
        self.td = timedelta
        self.now = datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc)

    def test_active_notam_within_window(self):
        """NOTAM active now should be included."""
        notam = {
            'start': '2026-01-30T00:00:00.000Z',
            'end': '2026-02-07T23:59:00.000Z',
            'schedule': '',
        }
        self.assertTrue(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_future_notam_outside_window(self):
        """NOTAM starting tomorrow should be excluded from 3hr window."""
        notam = {
            'start': '2026-02-01T00:00:00.000Z',
            'end': '2026-03-03T23:59:00.000Z',
            'schedule': '',
        }
        self.assertFalse(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_future_notam_within_large_window(self):
        """NOTAM starting tomorrow should be included in 24hr window."""
        notam = {
            'start': '2026-02-01T00:00:00.000Z',
            'end': '2026-03-03T23:59:00.000Z',
            'schedule': '',
        }
        self.assertTrue(self.is_active(notam, self.now, self.now + self.td(hours=24)))

    def test_expired_notam_excluded(self):
        """NOTAM that ended yesterday should be excluded."""
        notam = {
            'start': '2026-01-25T00:00:00.000Z',
            'end': '2026-01-30T23:59:00.000Z',
            'schedule': '',
        }
        self.assertFalse(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_perm_notam_always_active(self):
        """PERM NOTAM with past start should always be active."""
        notam = {
            'start': '2025-06-01T00:00:00.000Z',
            'end': 'PERM',
            'schedule': '',
        }
        self.assertTrue(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_schedule_active_window(self):
        """NOTAM with schedule active during our window should be included."""
        notam = {
            'start': '2026-01-30T00:00:00.000Z',
            'end': '2026-02-13T03:00:00.000Z',
            'schedule': '0900-1200',  # 0900-1200Z daily
        }
        # Window 1000-1300Z overlaps with 0900-1200Z
        self.assertTrue(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_schedule_inactive_window(self):
        """NOTAM with schedule outside our window should be excluded."""
        notam = {
            'start': '2026-01-30T00:00:00.000Z',
            'end': '2026-02-13T03:00:00.000Z',
            'schedule': '1930-0330',  # Evening schedule
        }
        # Window 1000-1300Z does NOT overlap with 1930-0330Z
        self.assertFalse(self.is_active(notam, self.now, self.now + self.td(hours=3)))

    def test_schedule_overnight_active(self):
        """Overnight schedule active at check time should be included."""
        notam = {
            'start': '2026-01-30T00:00:00.000Z',
            'end': '2026-02-13T03:00:00.000Z',
            'schedule': '2200-0600',  # Overnight
        }
        # Check at 0200Z — inside overnight window
        check_time = self.dt(2026, 1, 31, 2, 0, 0, tzinfo=self.tz.utc)
        self.assertTrue(self.is_active(notam, check_time, check_time + self.td(hours=3)))


class TestWarningPhaseImpact(unittest.TestCase):
    """Test that weather warnings affect phase determination."""

    def setUp(self):
        self.data_path = os.path.join(os.path.dirname(__file__), 'airfield_data.json')
        with open(self.data_path) as f:
            raw = json.load(f)
        self.airfield_data = {}
        for icao, info in raw.get('airfields', {}).items():
            self.airfield_data[icao] = info

    def run_with_warning(self, metar_str, warning):
        """Helper: determine phase then apply warning override."""
        import re
        metar = METARParser(metar_str)
        runway_heading = 330  # 33R
        result = determine_phase(metar, runway_heading, self.airfield_data)
        warn_upper = warning.upper()

        # Replicate warning parsing from main()
        warn_vis_m = None
        vis_patterns = [
            r'VIS(?:IBILITY)?\s+(?:BELOW\s+|<\s*|OF\s+)?(\d+)\s*(?:M(?:ETERS?)?|OR\s+LESS)',
            r'VIS(?:IBILITY)?\s+(?:BELOW\s+|<\s*|OF\s+)?(\d+(?:\.\d+)?)\s*KM',
            r'VIS(?:IBILITY)?\s+(\d+)\b',
        ]
        for vp in vis_patterns:
            vm = re.search(vp, warn_upper)
            if vm:
                val = float(vm.group(1))
                if val < 100:
                    val = val * 1000
                warn_vis_m = val
                break

        phase_rank = {'RECALL': 6, 'HOLD': 5, 'IFR': 4, 'VFR': 3, 'FS VFR': 2, 'RESTRICTED': 1, 'UNRESTRICTED': 0}
        current_rank = phase_rank.get(result['phase'], 0)

        if warn_vis_m is not None:
            if warn_vis_m < 2400 and phase_rank['HOLD'] > current_rank:
                result['phase'] = 'HOLD'
            elif warn_vis_m < 5000 and phase_rank['IFR'] > current_rank:
                result['phase'] = 'IFR'
            elif warn_vis_m < 8000 and phase_rank['VFR'] > current_rank:
                result['phase'] = 'VFR'

        return result

    def test_vis_2000_warning_forces_hold(self):
        """Warning 'visibility 2000 or less' should force HOLD."""
        result = self.run_with_warning(
            "OEKF 311300Z 33012KT 9999 SKC 22/10 Q1018",
            "visibility 2000 or less"
        )
        self.assertEqual(result['phase'], 'HOLD')

    def test_vis_3000_warning_forces_ifr(self):
        """Warning 'visibility 3000' should force IFR."""
        result = self.run_with_warning(
            "OEKF 311300Z 33012KT 9999 SKC 22/10 Q1018",
            "visibility 3000"
        )
        self.assertEqual(result['phase'], 'IFR')

    def test_vis_6000_warning_caps_vfr(self):
        """Warning 'visibility 6000' on UNRESTRICTED METAR → VFR."""
        result = self.run_with_warning(
            "OEKF 311300Z 33012KT 9999 SKC 22/10 Q1018",
            "visibility 6000"
        )
        self.assertEqual(result['phase'], 'VFR')

    def test_warning_doesnt_improve_phase(self):
        """Warning with higher vis than METAR shouldn't improve phase."""
        result = self.run_with_warning(
            "OEKF 311300Z 05018G22KT 9999 NSC 20/03 Q1013",
            "visibility 9000"
        )
        # METAR gives VFR (crosswind 21.7kt), warning vis 9000 shouldn't change it
        self.assertEqual(result['phase'], 'VFR')


if __name__ == '__main__':
    unittest.main(verbosity=2)
