"""Tests for efficiency computation functions and charts."""

import datetime
import json
from unittest.mock import AsyncMock, patch

import pytest
from helpers import (
    compute_monthly_average_efficiency,
    compute_normalized_efficiency,
    filter_efficiency_data,
    group_sessions_by_mileage,
)


class TestEfficiencyFunctions:
    """Test suite for efficiency-related helper functions."""

    def test_filter_efficiency_data_empty(self):
        """Test filtering with empty input."""
        result = filter_efficiency_data([])
        assert result == []

    def test_filter_efficiency_data_excludes_low_efficiency(self):
        """Test that efficiency below 150 km is filtered out."""
        data = [
            {
                "estimated_efficiency": 100,  # Below 150 - should be filtered
                "actual_efficiency": 100,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 15),
            }
        ]
        result = filter_efficiency_data(data)
        assert len(result) == 0

    def test_filter_efficiency_data_excludes_high_efficiency(self):
        """Test that efficiency above 550 km is filtered out."""
        data = [
            {
                "estimated_efficiency": 600,  # Above 550 - should be filtered
                "actual_efficiency": 600,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 15),
            }
        ]
        result = filter_efficiency_data(data)
        assert len(result) == 0

    def test_filter_efficiency_data_excludes_low_soc_gain(self):
        """Test that SOC gain below 20% is filtered out."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 300,
                "soc_gain": 15,  # Below 20% - should be filtered
                "stop_at": datetime.datetime(2025, 1, 15),
            }
        ]
        result = filter_efficiency_data(data)
        assert len(result) == 0

    def test_filter_efficiency_data_keeps_valid(self):
        """Test that valid data is kept."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 300,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            }
        ]
        result = filter_efficiency_data(data)
        assert len(result) == 1
        assert result[0] == data[0]

    def test_filter_efficiency_data_mixed(self):
        """Test filtering with mixed valid and invalid data."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 300,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            },
            {
                "estimated_efficiency": 100,  # Below 150
                "actual_efficiency": 100,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 16),
            },
            {
                "estimated_efficiency": 350,
                "actual_efficiency": 350,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 17),
            },
        ]
        result = filter_efficiency_data(data)
        assert len(result) == 2
        assert result[0]["estimated_efficiency"] == 300
        assert result[1]["estimated_efficiency"] == 350

    def test_compute_monthly_average_efficiency_empty(self):
        """Test computing averages with no data."""
        result = compute_monthly_average_efficiency([], 2025, 1)
        assert result["estimated_efficiency_avg"] is None
        assert result["actual_efficiency_avg"] is None
        assert result["count"] == 0

    def test_compute_monthly_average_efficiency_single_month(self):
        """Test computing averages for a single month."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15, 12, 0, 0),
            },
            {
                "estimated_efficiency": 320,
                "actual_efficiency": 310,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 20, 12, 0, 0),
            },
        ]
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 2
        assert result["estimated_efficiency_avg"] == 310.0  # (300 + 320) / 2
        assert result["actual_efficiency_avg"] == 300.0  # (290 + 310) / 2

    def test_compute_monthly_average_efficiency_filters_by_month(self):
        """Test that monthly averaging filters by correct month/year."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            },
            {
                "estimated_efficiency": 320,
                "actual_efficiency": 310,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 2, 15),  # Different month
            },
        ]
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 1
        assert result["estimated_efficiency_avg"] == 300.0
        assert result["actual_efficiency_avg"] == 290.0

    def test_compute_monthly_average_efficiency_with_none_values(self):
        """Test that None values are handled correctly."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": None,  # Missing actual efficiency
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            },
            {
                "estimated_efficiency": 320,
                "actual_efficiency": 310,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 20),
            },
        ]
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 2
        assert result["estimated_efficiency_avg"] == 310.0
        assert result["actual_efficiency_avg"] == 310.0  # Only 1 value for actual

    def test_compute_monthly_average_efficiency_boundary_dates(self):
        """Test that month boundaries are handled correctly."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 1, 0, 0, 1),  # Start of month
            },
            {
                "estimated_efficiency": 320,
                "actual_efficiency": 310,
                "soc_gain": 30,
                "stop_at": datetime.datetime(2025, 1, 31, 23, 59, 59),  # End of month
            },
            {
                "estimated_efficiency": 350,
                "actual_efficiency": 340,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 2, 1, 0, 0, 0),  # Next month starts
            },
        ]
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 2  # Only first two should be counted
        assert result["estimated_efficiency_avg"] == 310.0

    def test_compute_monthly_average_efficiency_february_leap_year(self):
        """Test that February boundaries work correctly in leap years."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2024, 2, 29, 12, 0, 0),  # Leap year
            }
        ]
        result = compute_monthly_average_efficiency(data, 2024, 2)
        assert result["count"] == 1

    def test_filter_efficiency_applies_to_monthly_average(self):
        """Test that monthly averaging also filters data."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            },
            {
                "estimated_efficiency": 100,  # Invalid - below 150
                "actual_efficiency": 100,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 20),
            },
        ]
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 1  # Only valid entry counted
        assert result["estimated_efficiency_avg"] == 300.0

    def test_monthly_averages_for_all_months(self):
        """Test computing averages for all 12 months."""
        data = []
        for month in range(1, 13):
            data.append(
                {
                    "estimated_efficiency": 300 + month * 10,
                    "actual_efficiency": 290 + month * 10,
                    "soc_gain": 25,
                    "stop_at": datetime.datetime(2025, month, 15),
                }
            )

        for month in range(1, 13):
            result = compute_monthly_average_efficiency(data, 2025, month)
            assert result["count"] == 1
            assert result["estimated_efficiency_avg"] == 300 + month * 10
            assert result["actual_efficiency_avg"] == 290 + month * 10

    def test_monthly_averages_handles_missing_months(self):
        """Test that months with no data return None."""
        data = [
            {
                "estimated_efficiency": 300,
                "actual_efficiency": 290,
                "soc_gain": 25,
                "stop_at": datetime.datetime(2025, 1, 15),
            }
        ]
        # January should have data
        result = compute_monthly_average_efficiency(data, 2025, 1)
        assert result["count"] == 1

        # February should have no data
        result = compute_monthly_average_efficiency(data, 2025, 2)
        assert result["count"] == 0
        assert result["estimated_efficiency_avg"] is None
