"""
Unit tests for the NetworkController class.

These tests verify the correctness of the network speed calculation,
interface filtering, and aggregation logic.
"""

import pytest
from unittest.mock import patch, MagicMock
import time

from netspeedtray import constants
from netspeedtray.core.controller import NetworkController
from netspeedtray.core.widget_state import WidgetState

# A helper class to simulate psutil's snetio counter objects
class MockNetIO:
    """A simple mock for psutil._common.snetio."""
    def __init__(self, bytes_sent: int, bytes_recv: int):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv

@pytest.fixture
def mock_config() -> dict:
    """Provides a default configuration dictionary for tests."""
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    # Let's add a specific exclusion for one of our tests
    config["excluded_interfaces"].append("test_exclude")
    return config

@pytest.fixture
def mock_widget_state() -> MagicMock:
    """Provides a MagicMock of the WidgetState."""
    return MagicMock(spec=WidgetState)

@pytest.fixture
def controller_instance(mock_config: dict, mock_widget_state: MagicMock) -> NetworkController:
    """Provides a fresh, configured NetworkController instance for each test."""
    return NetworkController(config=mock_config, widget_state=mock_widget_state)

def test_update_speeds_calculates_and_emits_correctly(controller_instance, mock_widget_state):
    """
    Tests the core logic of update_speeds, ensuring it calculates speeds,
    respects exclusions, and sends correct data to the data and view layers.
    """
    # ARRANGE
    controller = controller_instance
    
    # Manually initialize the controller's internal state to simulate
    # that a "priming read" has already occurred. This is the key fix.
    controller.last_check_time = time.monotonic()
    controller.last_interface_counters = {
        "Wi-Fi": MockNetIO(bytes_sent=1000, bytes_recv=2000),
        "Ethernet": MockNetIO(bytes_sent=500, bytes_recv=800),
        "test_exclude": MockNetIO(bytes_sent=9999, bytes_recv=9999) 
    }
    controller.primary_interface = None # Ensure it uses the fallback aggregation logic

    # Define the network counters for the second read
    second_counters = {
        "Wi-Fi": MockNetIO(bytes_sent=3000, bytes_recv=6000),    # Diff: 2000 sent, 4000 recv
        "Ethernet": MockNetIO(bytes_sent=1100, bytes_recv=1800),  # Diff: 600 sent, 1000 recv
        "test_exclude": MockNetIO(bytes_sent=9999, bytes_recv=9999)
    }
    
    mock_view = MagicMock()
    controller.set_view(mock_view)

    # ACT
    # Simulate the passage of 2 seconds and the new psutil data
    with patch('time.monotonic', return_value=controller.last_check_time + 2.0):
        with patch('psutil.net_io_counters', return_value=second_counters):
            controller.update_speeds()

    # ASSERT
    mock_widget_state.add_speed_data.assert_called_once()
    mock_view.update_display_speeds.assert_called_once()
    display_args = mock_view.update_display_speeds.call_args[0]
    
    # Total Upload B/s = (2000 + 600) / 2s = 1300
    # Total Upload Mbps = (1300 * 8) / 1,000,000 = 0.0104
    assert display_args[0] == pytest.approx(0.0104)
    # Total Download B/s = (4000 + 1000) / 2s = 2500
    # Total Download Mbps = (2500 * 8) / 1,000,000 = 0.02
    assert display_args[1] == pytest.approx(0.02)

def test_update_speeds_handles_resume_from_sleep(controller_instance, mock_widget_state):
    """
    Tests that a long time delta re-primes counters and emits zero speed.
    """
    # ARRANGE
    controller = controller_instance
    initial_counters = { "Wi-Fi": MockNetIO(bytes_sent=1000, bytes_recv=2000) }
    with patch('psutil.net_io_counters', return_value=initial_counters):
        controller.update_speeds()

    second_counters = { "Wi-Fi": MockNetIO(bytes_sent=1500, bytes_recv=2500) }
    mock_view = MagicMock()
    controller.set_view(mock_view)
    
    # Store the time *before* the long sleep
    time_before_sleep = controller.last_check_time
    
    # ACT
    with patch('time.monotonic', return_value=time_before_sleep + 600.0):
        with patch('psutil.net_io_counters', return_value=second_counters):
            controller.update_speeds()

    # ASSERT
    mock_widget_state.add_speed_data.assert_not_called()
    mock_view.update_display_speeds.assert_called_once_with(0.0, 0.0)
    # Assert the time has been updated to the new baseline
    assert controller.last_check_time == time_before_sleep + 600.0

def test_aggregate_for_display_select_specific_mode(controller_instance):
    """
    Tests that the _aggregate_for_display method correctly sums only the user-selected
    interfaces when the monitoring mode is 'Select Specific'.
    """
    # ARRANGE
    controller = controller_instance
    
    # Configure the controller to be in "Select Specific" mode
    controller.config["interface_mode"] = "Select Specific"
    controller.config["selected_interfaces"] = ["Wi-Fi", "VPN"] # User wants to see these two
    
    # Provide a dictionary of calculated per-interface speeds (in Bytes/sec)
    per_interface_speeds = {
        "Wi-Fi": (1000.0, 2000.0),
        "Ethernet": (5000.0, 8000.0), # This one should be IGNORED
        "VPN": (100.0, 150.0),
        "Bluetooth": (5.0, 10.0)      # This one should be IGNORED
    }
    
    # ACT
    agg_upload_bps, agg_download_bps = controller._aggregate_for_display(per_interface_speeds)
    
    # ASSERT
    # The result should be the sum of "Wi-Fi" and "VPN" only.
    # Expected Upload = 1000.0 (Wi-Fi) + 100.0 (VPN) = 1100.0 B/s
    assert agg_upload_bps == pytest.approx(1100.0)
    
    # Expected Download = 2000.0 (Wi-Fi) + 150.0 (VPN) = 2150.0 B/s
    assert agg_download_bps == pytest.approx(2150.0)